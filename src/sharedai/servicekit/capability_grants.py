"""Receiver-side verification for single-use capability grants.

This module deliberately owns only wire validation, cryptographic verification,
HTTP request reconstruction, and the neutral redeem callback boundary. Issuance,
policy, approval, lease, and lifecycle decisions remain with the authority that
issued the grant.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import inspect
import json
import math
import re
import ssl
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Protocol, TypeAlias, runtime_checkable
from urllib.parse import quote

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictStr, ValidationError, field_validator

from sharedai.servicekit.auth import read_secret_file

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_HTTP_TOKEN_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_METHOD_RE = re.compile(r"^[A-Z][A-Z0-9!#$%&'*+.^_`|~-]*$")

GRANT_HEADER = "X-AI-Local-Capability-Grant"
GRANT_ID_HEADER = "X-AI-Local-Capability-Grant-ID"
TASK_ID_HEADER = "X-Task-ID"
TRACE_ID_HEADER = "X-Trace-ID"
ACTION_ID_HEADER = "X-Agentic-Action-ID"
ATTEMPT_ID_HEADER = "X-Agentic-Node-Attempt"
IDEMPOTENCY_HEADER = "Idempotency-Key"
BROKER_TOKEN_HEADER = "X-Internal-Token"
BROKER_RECEIVER_HEADER = "X-AI-Local-Capability-Receiver"

_BOUND_HEADERS = {
    GRANT_ID_HEADER: "grant_id",
    TASK_ID_HEADER: "task_id",
    TRACE_ID_HEADER: "trace_id",
    ACTION_ID_HEADER: "action_id",
    ATTEMPT_ID_HEADER: "attempt_id",
    IDEMPOTENCY_HEADER: "idempotency_key",
}


class CapabilityGrantError(ValueError):
    """Base error with a stable, non-secret failure code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CapabilityGrantFormatError(CapabilityGrantError):
    """The compact token, claims, request, or configuration is malformed."""


class CapabilityGrantDenied(CapabilityGrantError, PermissionError):
    """A well-formed grant does not authorize this exact receiver request."""


class CapabilityGrantRedeemUnavailable(RuntimeError):
    """The configured single-use authority could not produce a reliable answer."""


def _validate_json_value(value: Any, *, path: str = "$") -> None:
    if value is None or isinstance(value, (bool, str)):
        if isinstance(value, str):
            try:
                value.encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                raise CapabilityGrantFormatError("json_invalid_unicode") from exc
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CapabilityGrantFormatError("json_non_finite_number")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CapabilityGrantFormatError("json_object_key_must_be_string")
            _validate_json_value(key, path=f"{path}.<key>")
            _validate_json_value(item, path=f"{path}.{key}")
        return
    raise CapabilityGrantFormatError("json_value_not_supported")


def canonical_json_bytes(value: JsonValue) -> bytes:
    """Serialize an already-JSON value deterministically and without coercion."""

    _validate_json_value(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise CapabilityGrantFormatError("json_canonicalization_failed") from exc


def canonical_json(value: JsonValue) -> str:
    """Return the UTF-8 canonical JSON representation as text."""

    return canonical_json_bytes(value).decode("utf-8")


def canonical_sha256(value: JsonValue) -> str:
    """Hash the canonical JSON representation with SHA-256."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def token_fingerprint(token: str) -> str:
    """Return a non-reversible fingerprint suitable for redemption/audit payloads."""

    if not isinstance(token, str) or not token:
        raise CapabilityGrantFormatError("capability_grant_token_missing")
    try:
        encoded = token.encode("ascii", errors="strict")
    except UnicodeEncodeError as exc:
        raise CapabilityGrantFormatError("capability_grant_token_not_ascii") from exc
    return hashlib.sha256(encoded).hexdigest()


def b64url_encode(value: bytes) -> str:
    """Encode bytes as canonical unpadded base64url."""

    if not isinstance(value, bytes):
        raise TypeError("b64url_encode requires bytes")
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    """Decode only canonical, non-empty, unpadded base64url."""

    if not isinstance(value, str) or not value or not _B64URL_RE.fullmatch(value):
        raise CapabilityGrantFormatError("invalid_base64url")
    if len(value) % 4 == 1:
        raise CapabilityGrantFormatError("invalid_base64url")
    try:
        decoded = base64.b64decode(value + ("=" * (-len(value) % 4)), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CapabilityGrantFormatError("invalid_base64url") from exc
    if b64url_encode(decoded) != value:
        raise CapabilityGrantFormatError("non_canonical_base64url")
    return decoded


def _reject_json_constant(_: str) -> None:
    raise CapabilityGrantFormatError("json_non_finite_number")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise CapabilityGrantFormatError("json_duplicate_object_key")
        value[key] = item
    return value


def strict_json_loads(value: bytes | str) -> JsonValue:
    """Parse UTF-8 JSON while rejecting duplicate keys and non-JSON numbers."""

    try:
        text = value.decode("utf-8", errors="strict") if isinstance(value, bytes) else value
    except UnicodeDecodeError as exc:
        raise CapabilityGrantFormatError("json_not_utf8") from exc
    if not isinstance(text, str) or not text:
        raise CapabilityGrantFormatError("json_missing")
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except CapabilityGrantError:
        raise
    except (json.JSONDecodeError, TypeError, UnicodeError) as exc:
        raise CapabilityGrantFormatError("json_invalid") from exc
    _validate_json_value(parsed)
    return parsed


class CapabilityGrantTransport(BaseModel):
    """The exact receiver transport bound into a capability grant."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    type: StrictStr = Field(min_length=1, max_length=80)
    service: StrictStr = Field(min_length=1, max_length=200)
    method: StrictStr = Field(min_length=1, max_length=32)
    path: StrictStr = Field(min_length=1, max_length=1000)
    auth_profile: StrictStr | None = Field(default=None, max_length=200)
    tls_alias_profile: StrictStr | None = Field(default=None, max_length=200)

    @field_validator("type", "service", "auth_profile", "tls_alias_profile")
    @classmethod
    def validate_identifier(cls, value: str | None) -> str | None:
        if value is not None and (value != value.strip() or any(ord(char) < 0x20 for char in value)):
            raise ValueError("transport identifiers must be trimmed and contain no control characters")
        return value

    @field_validator("method")
    @classmethod
    def validate_method(cls, value: str) -> str:
        if not _METHOD_RE.fullmatch(value):
            raise ValueError("transport method must be an uppercase HTTP token")
        return value

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if not value.startswith("/") or "?" in value or "#" in value or "\\" in value:
            raise ValueError("transport path must be an absolute URL path without query or fragment")
        if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
            raise ValueError("transport path contains control characters")
        return value


class CapabilityGrantRedemptionRequest(BaseModel):
    """Neutral, secret-free payload sent to the single-use authority callback."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    grant_id: StrictStr = Field(min_length=1, max_length=200)
    grant_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    token_fingerprint: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    receiver_id: StrictStr = Field(min_length=1, max_length=200)
    audience: StrictStr = Field(min_length=1, max_length=200)
    request_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    transport: CapabilityGrantTransport


@runtime_checkable
class CapabilityGrantRedeemer(Protocol):
    """Authority callback; only an explicit ``True`` authorizes the handler."""

    def __call__(
        self,
        redemption: CapabilityGrantRedemptionRequest,
        *,
        auth_headers: Mapping[str, str],
    ) -> bool | Awaitable[bool]: ...


class _RequiredGrantClaims(BaseModel):
    """Only receiver-neutral claims are interpreted; all other signed claims stay opaque."""

    model_config = ConfigDict(extra="allow", frozen=True, strict=True)

    schema_version: Literal["capability-grant-v1"]
    grant_id: StrictStr = Field(min_length=1, max_length=200)
    task_id: StrictStr = Field(min_length=1, max_length=160)
    trace_id: StrictStr = Field(min_length=1, max_length=160)
    action_id: StrictStr = Field(min_length=1, max_length=160)
    attempt_id: StrictStr = Field(min_length=1, max_length=500)
    idempotency_key: StrictStr = Field(min_length=1, max_length=1000)
    audience: StrictStr = Field(min_length=1, max_length=200)
    request_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    transport: CapabilityGrantTransport
    issued_at: StrictFloat = Field(ge=0)
    not_before: StrictFloat = Field(ge=0)
    expires_at: StrictFloat = Field(gt=0)
    max_uses: Literal[1]
    issuer: StrictStr = Field(min_length=1, max_length=300)
    key_id: StrictStr = Field(min_length=1, max_length=200)
    signature_algorithm: Literal["Ed25519"]
    grant_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")


def _freeze_json(value: JsonValue) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


@dataclass(frozen=True)
class VerifiedCapabilityGrant:
    """Locally verified grant and its opaque, immutable signed claim set."""

    grant_id: str
    task_id: str
    trace_id: str
    action_id: str
    attempt_id: str
    idempotency_key: str
    audience: str
    issuer: str
    key_id: str
    request_hash: str
    grant_hash: str
    issued_at: float
    not_before: float
    expires_at: float
    transport: CapabilityGrantTransport
    claims: Mapping[str, Any]


def public_key_from_base64url(value: str) -> Ed25519PublicKey:
    """Load one raw 32-byte Ed25519 public key from strict base64url."""

    raw = b64url_decode(value.strip() if isinstance(value, str) else value)
    if len(raw) != 32:
        raise CapabilityGrantFormatError("ed25519_public_key_length_invalid")
    return Ed25519PublicKey.from_public_bytes(raw)


def _coerce_public_key(value: Ed25519PublicKey | bytes | str) -> Ed25519PublicKey:
    if isinstance(value, Ed25519PublicKey):
        return value
    if isinstance(value, bytes):
        if len(value) != 32:
            raise CapabilityGrantFormatError("ed25519_public_key_length_invalid")
        return Ed25519PublicKey.from_public_bytes(value)
    if isinstance(value, str):
        return public_key_from_base64url(value)
    raise CapabilityGrantFormatError("ed25519_public_key_invalid")


def ed25519_public_key_id(value: Ed25519PublicKey | bytes | str) -> str:
    """Derive the canonical issuer-compatible ID from a raw Ed25519 public key."""

    public_key = _coerce_public_key(value)
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return f"ed25519:{hashlib.sha256(raw).hexdigest()[:32]}"


@dataclass(frozen=True)
class CapabilityGrantVerifier:
    """Verify one issuer/key/audience trust tuple against the exact request."""

    issuer: str
    key_id: str
    audience: str
    public_key: Ed25519PublicKey | bytes | str

    def __post_init__(self) -> None:
        for value, code in (
            (self.issuer, "capability_grant_issuer_missing"),
            (self.key_id, "capability_grant_key_id_missing"),
            (self.audience, "capability_grant_audience_missing"),
        ):
            if not isinstance(value, str) or not value or value != value.strip():
                raise CapabilityGrantFormatError(code)
        object.__setattr__(self, "public_key", _coerce_public_key(self.public_key))

    def verify(
        self,
        token: str,
        *,
        request_payload: JsonValue,
        transport: CapabilityGrantTransport | Mapping[str, Any],
        headers: Mapping[str, str],
        now: float | None = None,
    ) -> VerifiedCapabilityGrant:
        """Verify signature, canonical claims, trust tuple, request, transport, and headers."""

        if not isinstance(token, str) or token.count(".") != 1:
            raise CapabilityGrantFormatError("invalid_capability_grant_token")
        payload_part, signature_part = token.split(".")
        payload_bytes = b64url_decode(payload_part)
        signature = b64url_decode(signature_part)
        if len(signature) != 64:
            raise CapabilityGrantFormatError("ed25519_signature_length_invalid")
        try:
            self.public_key.verify(signature, payload_bytes)
        except InvalidSignature as exc:
            raise CapabilityGrantDenied("capability_grant_signature_invalid") from exc

        payload = strict_json_loads(payload_bytes)
        if not isinstance(payload, dict):
            raise CapabilityGrantFormatError("capability_grant_claims_not_object")
        if canonical_json_bytes(payload) != payload_bytes:
            raise CapabilityGrantFormatError("capability_grant_claims_not_canonical")
        if "signature" in payload:
            raise CapabilityGrantFormatError("capability_grant_signature_must_be_detached")
        try:
            claims = _RequiredGrantClaims.model_validate(payload)
        except ValidationError as exc:
            raise CapabilityGrantFormatError("capability_grant_claims_invalid") from exc

        unsigned_claims = dict(payload)
        unsigned_claims.pop("grant_hash", None)
        expected_grant_hash = canonical_sha256(unsigned_claims)
        if claims.grant_hash != expected_grant_hash:
            raise CapabilityGrantDenied("capability_grant_hash_mismatch")
        if claims.issuer != self.issuer:
            raise CapabilityGrantDenied("capability_grant_issuer_mismatch")
        if claims.key_id != self.key_id:
            raise CapabilityGrantDenied("capability_grant_key_mismatch")
        if claims.audience != self.audience:
            raise CapabilityGrantDenied("capability_grant_audience_mismatch")
        if claims.not_before < claims.issued_at or claims.expires_at <= claims.not_before:
            raise CapabilityGrantFormatError("capability_grant_time_bounds_invalid")

        current = time.time() if now is None else float(now)
        if not math.isfinite(current):
            raise CapabilityGrantFormatError("capability_grant_now_invalid")
        if current < claims.not_before:
            raise CapabilityGrantDenied("capability_grant_not_yet_valid")
        if current >= claims.expires_at:
            raise CapabilityGrantDenied("capability_grant_expired")

        expected_request_hash = canonical_sha256(request_payload)
        if claims.request_hash != expected_request_hash:
            raise CapabilityGrantDenied("capability_grant_request_mismatch")
        try:
            expected_transport = CapabilityGrantTransport.model_validate(transport)
        except ValidationError as exc:
            raise CapabilityGrantFormatError("capability_grant_expected_transport_invalid") from exc
        if claims.transport != expected_transport:
            raise CapabilityGrantDenied("capability_grant_transport_mismatch")
        self._verify_bound_headers(claims, headers)

        return VerifiedCapabilityGrant(
            grant_id=claims.grant_id,
            task_id=claims.task_id,
            trace_id=claims.trace_id,
            action_id=claims.action_id,
            attempt_id=claims.attempt_id,
            idempotency_key=claims.idempotency_key,
            audience=claims.audience,
            issuer=claims.issuer,
            key_id=claims.key_id,
            request_hash=claims.request_hash,
            grant_hash=claims.grant_hash,
            issued_at=claims.issued_at,
            not_before=claims.not_before,
            expires_at=claims.expires_at,
            transport=claims.transport,
            claims=_freeze_json(payload),
        )

    @staticmethod
    def _verify_bound_headers(claims: _RequiredGrantClaims, headers: Mapping[str, str]) -> None:
        for header_name, claim_name in _BOUND_HEADERS.items():
            provided = headers.get(header_name)
            if not isinstance(provided, str) or not provided:
                raise CapabilityGrantDenied("capability_grant_bound_header_missing")
            if provided != getattr(claims, claim_name):
                raise CapabilityGrantDenied("capability_grant_bound_header_mismatch")


def _query_value_pairs(params: Mapping[str, Any] | Sequence[tuple[str, Any]]) -> list[tuple[str, str]]:
    pairs = params.items() if isinstance(params, Mapping) else params
    result: list[tuple[str, str]] = []
    for raw_key, raw_value in pairs:
        key = str(raw_key)
        values = raw_value if isinstance(raw_value, (list, tuple)) else [raw_value]
        for value in values:
            if value is None:
                text = ""
            elif isinstance(value, bool):
                text = "true" if value else "false"
            elif isinstance(value, bytes):
                try:
                    text = value.decode("utf-8", errors="strict")
                except UnicodeDecodeError as exc:
                    raise CapabilityGrantFormatError("query_parameter_not_utf8") from exc
            else:
                text = str(value)
            result.append((key, text))
    return result


def canonical_query_params(params: Mapping[str, Any] | Sequence[tuple[str, Any]] | None) -> dict[str, JsonValue]:
    """Normalize query values to their reconstructable HTTP string representation."""

    if not params:
        return {}
    grouped: defaultdict[str, list[str]] = defaultdict(list)
    for key, value in _query_value_pairs(params):
        grouped[key].append(value)
    return {
        key: values[0] if len(values) == 1 else list(values)
        for key, values in sorted(grouped.items())
    }


def canonical_request_payload(
    *,
    method: str,
    body: JsonValue = None,
    params: Mapping[str, Any] | Sequence[tuple[str, Any]] | None = None,
) -> JsonValue:
    """Build the request value hashed by both sender and FastAPI receiver."""

    normalized_method = method.upper()
    if not _METHOD_RE.fullmatch(normalized_method):
        raise CapabilityGrantFormatError("request_method_invalid")
    normalized_params = canonical_query_params(params)
    _validate_json_value(body)
    if normalized_method in {"GET", "HEAD"}:
        if body is not None:
            raise CapabilityGrantFormatError("safe_method_body_not_supported")
        return normalized_params
    if normalized_params:
        return {"json": body, "params": normalized_params}
    return body


async def reconstruct_fastapi_request(request: Request) -> JsonValue:
    """Reconstruct the sender-canonical body/query value from one FastAPI request."""

    raw_body = await request.body()
    method = request.method.upper()
    if method in {"GET", "HEAD"} and raw_body:
        raise CapabilityGrantFormatError("safe_method_body_not_supported")

    body: JsonValue = None
    if raw_body:
        media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media_type != "application/json" and not media_type.endswith("+json"):
            raise CapabilityGrantFormatError("capability_request_content_type_not_json")
        body = strict_json_loads(raw_body)

    return canonical_request_payload(
        method=method,
        body=body,
        params=list(request.query_params.multi_items()),
    )


def reconstruct_fastapi_raw_path(request: Request) -> str:
    """Return the exact percent-encoded ASGI path used as transport authority."""

    raw_path = request.scope.get("raw_path")
    if not isinstance(raw_path, bytes) or not raw_path:
        raise CapabilityGrantFormatError("capability_request_raw_path_missing")
    try:
        path = raw_path.decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise CapabilityGrantFormatError(
            "capability_request_raw_path_not_ascii"
        ) from exc
    if (
        not path.startswith("/")
        or path.startswith("//")
        or "?" in path
        or "#" in path
        or "\\" in path
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in path)
    ):
        raise CapabilityGrantFormatError("capability_request_raw_path_invalid")
    return path


def broker_internal_token_headers(
    *,
    configured_token: str = "",
    token_file: str | Path = "",
    header_name: str = BROKER_TOKEN_HEADER,
    receiver_id: str = "",
) -> Mapping[str, str]:
    """Build fail-closed broker auth headers with optional receiver identity."""

    if not isinstance(header_name, str) or not _HTTP_TOKEN_RE.fullmatch(header_name):
        raise CapabilityGrantFormatError("broker_auth_header_name_invalid")
    token = configured_token.strip() if isinstance(configured_token, str) else ""
    if not token and token_file:
        token = read_secret_file(str(token_file))
    if not token:
        raise CapabilityGrantDenied("broker_auth_token_missing")
    if "\r" in token or "\n" in token:
        raise CapabilityGrantFormatError("broker_auth_token_invalid")
    headers = {header_name: token}
    if receiver_id:
        if (
            receiver_id != receiver_id.strip()
            or len(receiver_id) > 200
            or not _HTTP_TOKEN_RE.fullmatch(receiver_id)
        ):
            raise CapabilityGrantFormatError("broker_receiver_id_invalid")
        headers[BROKER_RECEIVER_HEADER] = receiver_id
    return MappingProxyType(headers)


class AsyncHTTPCapabilityGrantRedeemer:
    """POST neutral redemption payloads to an explicitly configured authority.

    The class does not choose an authority URL or endpoint. Consumers that do
    not inject a client must close the owned client with :meth:`aclose` during
    application shutdown.
    """

    def __init__(
        self,
        *,
        broker_base_url: str,
        endpoint_template: str,
        timeout: float = 5.0,
        verify: bool | str | ssl.SSLContext = True,
        allow_insecure_http: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        base_url = str(broker_base_url).strip().rstrip("/")
        endpoint = str(endpoint_template).strip()
        if not base_url:
            raise CapabilityGrantFormatError("broker_base_url_missing")
        try:
            parsed = httpx.URL(base_url)
        except Exception as exc:
            raise CapabilityGrantFormatError("broker_base_url_invalid") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.host
            or parsed.userinfo
            or parsed.query
            or parsed.fragment
            or (parsed.scheme != "https" and not allow_insecure_http)
        ):
            raise CapabilityGrantFormatError("broker_base_url_invalid")
        if not endpoint.startswith("/") or "?" in endpoint or "#" in endpoint:
            raise CapabilityGrantFormatError("broker_redeem_endpoint_invalid")
        if not math.isfinite(float(timeout)) or timeout <= 0:
            raise CapabilityGrantFormatError("broker_redeem_timeout_invalid")
        self._base_url = base_url
        self._endpoint_template = endpoint
        self._timeout = float(timeout)
        self._client = client or httpx.AsyncClient(timeout=self._timeout, verify=verify)
        self._owns_client = client is None

    async def __call__(
        self,
        redemption: CapabilityGrantRedemptionRequest,
        *,
        auth_headers: Mapping[str, str],
    ) -> bool:
        grant_id = quote(redemption.grant_id, safe="")
        try:
            endpoint = self._endpoint_template.format(grant_id=grant_id)
        except (KeyError, ValueError) as exc:
            raise CapabilityGrantFormatError("broker_redeem_endpoint_template_invalid") from exc
        if not endpoint.startswith("/") or "?" in endpoint or "#" in endpoint:
            raise CapabilityGrantFormatError("broker_redeem_endpoint_invalid")
        url = f"{self._base_url}{endpoint}"
        try:
            response = await self._client.post(
                url,
                json=redemption.model_dump(mode="json"),
                headers=dict(auth_headers),
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise CapabilityGrantRedeemUnavailable("capability_grant_redeem_transport_failed") from exc

        if response.status_code in {408, 429}:
            raise CapabilityGrantRedeemUnavailable("capability_grant_redeem_authority_busy")
        if 400 <= response.status_code < 500:
            return False
        if not 200 <= response.status_code < 300:
            raise CapabilityGrantRedeemUnavailable("capability_grant_redeem_authority_failed")
        try:
            payload = response.json()
        except (json.JSONDecodeError, UnicodeError, ValueError) as exc:
            raise CapabilityGrantRedeemUnavailable("capability_grant_redeem_response_invalid") from exc
        return bool(
            isinstance(payload, dict)
            and payload.get("redeemed") is True
            and payload.get("grant_id") == redemption.grant_id
            and isinstance(payload.get("event_id"), str)
            and payload["event_id"] == payload["event_id"].strip()
            and 0 < len(payload["event_id"]) <= 200
        )

    async def aclose(self) -> None:
        """Close the internally owned HTTP client; injected clients remain caller-owned."""

        if self._owns_client:
            await self._client.aclose()


RedeemAuthHeaders: TypeAlias = Mapping[str, str] | Callable[[], Mapping[str, str]] | None
RequiredGrantClaims: TypeAlias = Mapping[str, JsonValue] | None


def _resolved_auth_headers(source: RedeemAuthHeaders) -> Mapping[str, str]:
    if source is None:
        return MappingProxyType({})
    value = source() if callable(source) else source
    if not isinstance(value, Mapping):
        raise CapabilityGrantFormatError("broker_auth_headers_invalid")
    headers: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not _HTTP_TOKEN_RE.fullmatch(key):
            raise CapabilityGrantFormatError("broker_auth_header_name_invalid")
        if not isinstance(item, str) or not item or "\r" in item or "\n" in item:
            raise CapabilityGrantFormatError("broker_auth_header_value_invalid")
        headers[key] = item
    return MappingProxyType(headers)


def _required_claims_snapshot(source: RequiredGrantClaims) -> Mapping[str, bytes]:
    """Freeze route-specific opaque claims as canonical JSON bytes."""

    if source is None:
        return MappingProxyType({})
    if not isinstance(source, Mapping):
        raise CapabilityGrantFormatError("capability_grant_required_claims_invalid")
    snapshot: dict[str, bytes] = {}
    for key, value in source.items():
        if (
            not isinstance(key, str)
            or not key
            or key != key.strip()
            or len(key) > 200
            or any(ord(char) < 0x20 or ord(char) == 0x7F for char in key)
        ):
            raise CapabilityGrantFormatError("capability_grant_required_claims_invalid")
        try:
            snapshot[key] = canonical_json_bytes(value)
        except CapabilityGrantFormatError as exc:
            raise CapabilityGrantFormatError(
                "capability_grant_required_claims_invalid"
            ) from exc
    return MappingProxyType(snapshot)


def _thaw_json(value: Any) -> JsonValue:
    """Project an immutable verified claim back to canonical JSON types."""

    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _verify_required_claims(
    grant: VerifiedCapabilityGrant,
    required_claims: Mapping[str, bytes],
) -> None:
    """Deny an inexact signed claim before the grant can be redeemed."""

    for key, expected in required_claims.items():
        if key not in grant.claims:
            raise CapabilityGrantDenied("capability_grant_required_claim_mismatch")
        try:
            actual = canonical_json_bytes(_thaw_json(grant.claims[key]))
        except CapabilityGrantFormatError as exc:
            raise CapabilityGrantDenied(
                "capability_grant_required_claim_mismatch"
            ) from exc
        if actual != expected:
            raise CapabilityGrantDenied("capability_grant_required_claim_mismatch")


def _single_header(request: Request, name: str, *, required: bool = True) -> str:
    values = request.headers.getlist(name)
    if len(values) > 1:
        raise CapabilityGrantDenied("capability_grant_header_duplicated")
    value = values[0].strip() if values else ""
    if required and not value:
        raise CapabilityGrantDenied("capability_grant_header_missing")
    return value


def capability_grant_dependency(
    *,
    verifier: CapabilityGrantVerifier,
    receiver_id: str,
    transport_type: str,
    service: str,
    redeemer: CapabilityGrantRedeemer,
    auth_profile: str | None = None,
    tls_alias_profile: str | None = None,
    required_claims: RequiredGrantClaims = None,
    redeem_auth_headers: RedeemAuthHeaders = None,
    now: Callable[[], float] = time.time,
) -> Callable[[Request], Awaitable[VerifiedCapabilityGrant]]:
    """Build an async FastAPI dependency that verifies and redeems before routing."""

    for value, code in (
        (receiver_id, "capability_receiver_id_missing"),
        (transport_type, "capability_transport_type_missing"),
        (service, "capability_transport_service_missing"),
    ):
        if not isinstance(value, str) or not value or value != value.strip():
            raise CapabilityGrantFormatError(code)
    if not callable(redeemer):
        raise CapabilityGrantFormatError("capability_grant_redeemer_invalid")
    required_claims_snapshot = _required_claims_snapshot(required_claims)

    async def require_capability_grant(request: Request) -> VerifiedCapabilityGrant:
        try:
            token = _single_header(request, GRANT_HEADER)
            bound_headers = {name: _single_header(request, name) for name in _BOUND_HEADERS}
        except CapabilityGrantDenied as exc:
            raise HTTPException(status_code=401, detail=exc.code) from exc

        try:
            request_payload = await reconstruct_fastapi_request(request)
            transport = CapabilityGrantTransport(
                type=transport_type,
                service=service,
                method=request.method.upper(),
                path=reconstruct_fastapi_raw_path(request),
                auth_profile=auth_profile,
                tls_alias_profile=tls_alias_profile,
            )
            grant = verifier.verify(
                token,
                request_payload=request_payload,
                transport=transport,
                headers=bound_headers,
                now=now(),
            )
            _verify_required_claims(grant, required_claims_snapshot)
            redemption = CapabilityGrantRedemptionRequest(
                grant_id=grant.grant_id,
                grant_hash=grant.grant_hash,
                token_fingerprint=token_fingerprint(token),
                receiver_id=receiver_id,
                audience=grant.audience,
                request_hash=grant.request_hash,
                transport=transport,
            )
            authority_headers = _resolved_auth_headers(redeem_auth_headers)
        except CapabilityGrantFormatError as exc:
            raise HTTPException(status_code=400, detail=exc.code) from exc
        except CapabilityGrantDenied as exc:
            raise HTTPException(status_code=403, detail=exc.code) from exc
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail="capability_grant_request_invalid") from exc

        try:
            redeemed = redeemer(redemption, auth_headers=authority_headers)
            if inspect.isawaitable(redeemed):
                redeemed = await redeemed
        except CapabilityGrantDenied as exc:
            raise HTTPException(status_code=403, detail=exc.code) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="capability_grant_redeem_unavailable") from exc
        if redeemed is not True:
            raise HTTPException(status_code=403, detail="capability_grant_redeem_denied")
        return grant

    return require_capability_grant


__all__ = [
    "ACTION_ID_HEADER",
    "ATTEMPT_ID_HEADER",
    "BROKER_RECEIVER_HEADER",
    "BROKER_TOKEN_HEADER",
    "AsyncHTTPCapabilityGrantRedeemer",
    "CapabilityGrantDenied",
    "CapabilityGrantError",
    "CapabilityGrantFormatError",
    "CapabilityGrantRedeemUnavailable",
    "CapabilityGrantRedeemer",
    "CapabilityGrantRedemptionRequest",
    "CapabilityGrantTransport",
    "CapabilityGrantVerifier",
    "GRANT_HEADER",
    "GRANT_ID_HEADER",
    "IDEMPOTENCY_HEADER",
    "RequiredGrantClaims",
    "TASK_ID_HEADER",
    "TRACE_ID_HEADER",
    "VerifiedCapabilityGrant",
    "b64url_decode",
    "b64url_encode",
    "broker_internal_token_headers",
    "canonical_json",
    "canonical_json_bytes",
    "canonical_query_params",
    "canonical_request_payload",
    "canonical_sha256",
    "capability_grant_dependency",
    "ed25519_public_key_id",
    "public_key_from_base64url",
    "reconstruct_fastapi_raw_path",
    "reconstruct_fastapi_request",
    "strict_json_loads",
    "token_fingerprint",
]
