from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from sharedai.servicekit.capability_grants import (
    ACTION_ID_HEADER,
    ATTEMPT_ID_HEADER,
    GRANT_HEADER,
    GRANT_ID_HEADER,
    IDEMPOTENCY_HEADER,
    TASK_ID_HEADER,
    TRACE_ID_HEADER,
    AsyncHTTPCapabilityGrantRedeemer,
    CapabilityGrantDenied,
    CapabilityGrantError,
    CapabilityGrantFormatError,
    CapabilityGrantRedeemUnavailable,
    CapabilityGrantRedemptionRequest,
    CapabilityGrantTransport,
    CapabilityGrantVerifier,
    VerifiedCapabilityGrant,
    b64url_decode,
    b64url_encode,
    broker_internal_token_headers,
    canonical_json_bytes,
    canonical_request_payload,
    canonical_sha256,
    capability_grant_dependency,
    ed25519_public_key_id,
    strict_json_loads,
    token_fingerprint,
)
from sharedai.servicekit.auth import internal_service_api_key, verify_service_token

ISSUER = "test.capability.authority"
AUDIENCE = "receiver-service"
NOW = 1_800_000_000.0


def _transport(*, method: str = "POST", path: str = "/v1/work") -> CapabilityGrantTransport:
    return CapabilityGrantTransport(
        type="feature_endpoint",
        service="receiver-service",
        method=method,
        path=path,
        auth_profile="internal_api",
    )


def _claims(
    *,
    private_key: Ed25519PrivateKey,
    request_payload: Any,
    transport: CapabilityGrantTransport,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    public_key = private_key.public_key()
    claims: dict[str, Any] = {
        "schema_version": "capability-grant-v1",
        "grant_id": "grant-1",
        "task_id": "task-1",
        "trace_id": "trace-1",
        "action_id": "action-1",
        "attempt_id": "graph:task-1:node:root:attempt:1",
        "idempotency_key": "grant:task-1:action-1:attempt-1",
        "audience": AUDIENCE,
        "request_hash": canonical_sha256(request_payload),
        "transport": transport.model_dump(mode="json"),
        "issued_at": NOW - 10.0,
        "not_before": NOW - 9.0,
        "expires_at": NOW + 60.0,
        "max_uses": 1,
        "issuer": ISSUER,
        "key_id": ed25519_public_key_id(public_key),
        "signature_algorithm": "Ed25519",
        # Receiver-opaque issuer claims prove the neutral model does not encode policy semantics.
        "policy_event_id": "event-1",
        "lease_id": "lease-1",
        "permission_scopes": ["feature.invoke"],
    }
    if overrides:
        claims.update(overrides)
    claims["grant_hash"] = canonical_sha256(claims)
    return claims


def _signed_token(private_key: Ed25519PrivateKey, claims: Mapping[str, Any]) -> str:
    payload = canonical_json_bytes(dict(claims))
    return f"{b64url_encode(payload)}.{b64url_encode(private_key.sign(payload))}"


def _grant_bundle(
    *,
    request_payload: Any | None = None,
    transport: CapabilityGrantTransport | None = None,
    overrides: Mapping[str, Any] | None = None,
    private_key: Ed25519PrivateKey | None = None,
) -> tuple[Ed25519PrivateKey, CapabilityGrantVerifier, str, dict[str, str], Any, CapabilityGrantTransport]:
    key = private_key or Ed25519PrivateKey.generate()
    expected_request = {"query": "hello"} if request_payload is None else request_payload
    expected_transport = transport or _transport()
    claims = _claims(
        private_key=key,
        request_payload=expected_request,
        transport=expected_transport,
        overrides=overrides,
    )
    token = _signed_token(key, claims)
    headers = {
        GRANT_ID_HEADER: claims["grant_id"],
        TASK_ID_HEADER: claims["task_id"],
        TRACE_ID_HEADER: claims["trace_id"],
        ACTION_ID_HEADER: claims["action_id"],
        ATTEMPT_ID_HEADER: claims["attempt_id"],
        IDEMPOTENCY_HEADER: claims["idempotency_key"],
    }
    verifier = CapabilityGrantVerifier(
        issuer=ISSUER,
        key_id=ed25519_public_key_id(key.public_key()),
        audience=AUDIENCE,
        public_key=key.public_key(),
    )
    return key, verifier, token, headers, expected_request, expected_transport


def test_verifier_is_byte_compatible_with_issuer_hash_and_signature_contract() -> None:
    _, verifier, token, headers, request_payload, transport = _grant_bundle()

    grant = verifier.verify(
        token,
        request_payload=request_payload,
        transport=transport,
        headers=headers,
        now=NOW,
    )

    assert grant.grant_id == "grant-1"
    assert grant.transport == transport
    assert grant.claims["policy_event_id"] == "event-1"
    with pytest.raises(TypeError):
        grant.claims["grant_id"] = "mutated"  # type: ignore[index]


def test_transport_is_frozen_strict_and_forbids_extra_fields() -> None:
    transport = _transport()
    minimal = CapabilityGrantTransport(type="dispatch", service="x", method="POST", path="/x")
    assert minimal.auth_profile is None
    assert minimal.tls_alias_profile is None
    with pytest.raises(ValidationError):
        transport.path = "/other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        CapabilityGrantTransport.model_validate({**transport.model_dump(), "extra": True})
    with pytest.raises(ValidationError):
        CapabilityGrantTransport(type="dispatch", service="x", method="post", path="/x")
    with pytest.raises(ValidationError):
        CapabilityGrantTransport(type="dispatch", service="x", method="POST", path="relative")


@pytest.mark.parametrize(
    "value",
    [
        {1: "not-a-string-key"},
        {"number": float("nan")},
        {"bytes": b"not-json"},
    ],
)
def test_canonical_json_rejects_non_json_values(value: Any) -> None:
    with pytest.raises(CapabilityGrantFormatError):
        canonical_json_bytes(value)


def test_base64url_and_json_decoders_reject_noncanonical_or_ambiguous_input() -> None:
    assert b64url_decode(b64url_encode(b"grant")) == b"grant"
    with pytest.raises(CapabilityGrantFormatError):
        b64url_decode("Z3JhbnQ=")
    with pytest.raises(CapabilityGrantFormatError, match="json_duplicate_object_key"):
        strict_json_loads('{"grant_id":"one","grant_id":"two"}')
    with pytest.raises(CapabilityGrantFormatError, match="json_non_finite_number"):
        strict_json_loads('{"value":NaN}')


def test_signed_duplicate_key_payload_is_rejected_even_with_valid_signature() -> None:
    key, _, token, headers, request_payload, transport = _grant_bundle()
    payload_part, _ = token.split(".")
    payload = b64url_decode(payload_part).decode("utf-8")
    duplicated = (payload[:-1] + ',"grant_id":"grant-shadow"}').encode()
    duplicate_token = f"{b64url_encode(duplicated)}.{b64url_encode(key.sign(duplicated))}"
    verifier = CapabilityGrantVerifier(
        issuer=ISSUER,
        key_id=ed25519_public_key_id(key.public_key()),
        audience=AUDIENCE,
        public_key=key.public_key(),
    )

    with pytest.raises(CapabilityGrantFormatError, match="json_duplicate_object_key"):
        verifier.verify(
            duplicate_token,
            request_payload=request_payload,
            transport=transport,
            headers=headers,
            now=NOW,
        )


def test_noncanonical_but_signed_json_payload_is_rejected() -> None:
    key, _, token, headers, request_payload, transport = _grant_bundle()
    payload = strict_json_loads(b64url_decode(token.split(".")[0]))
    noncanonical = json.dumps(payload, indent=2, ensure_ascii=False).encode()
    signed = f"{b64url_encode(noncanonical)}.{b64url_encode(key.sign(noncanonical))}"
    verifier = CapabilityGrantVerifier(
        issuer=ISSUER,
        key_id=ed25519_public_key_id(key.public_key()),
        audience=AUDIENCE,
        public_key=key.public_key(),
    )

    with pytest.raises(CapabilityGrantFormatError, match="claims_not_canonical"):
        verifier.verify(signed, request_payload=request_payload, transport=transport, headers=headers, now=NOW)


def test_tampered_signature_and_hash_are_rejected_independently() -> None:
    key, verifier, token, headers, request_payload, transport = _grant_bundle()
    payload, signature = token.split(".")
    raw_signature = bytearray(b64url_decode(signature))
    raw_signature[0] ^= 1
    with pytest.raises(CapabilityGrantDenied, match="signature_invalid"):
        verifier.verify(
            f"{payload}.{b64url_encode(bytes(raw_signature))}",
            request_payload=request_payload,
            transport=transport,
            headers=headers,
            now=NOW,
        )

    claims = _claims(private_key=key, request_payload=request_payload, transport=transport)
    claims["grant_hash"] = "0" * 64
    with pytest.raises(CapabilityGrantDenied, match="hash_mismatch"):
        verifier.verify(
            _signed_token(key, claims),
            request_payload=request_payload,
            transport=transport,
            headers=headers,
            now=NOW,
        )


def test_wrong_audience_key_id_and_public_key_fail_closed() -> None:
    key, _, token, headers, request_payload, transport = _grant_bundle()
    wrong_audience = CapabilityGrantVerifier(
        issuer=ISSUER,
        key_id=ed25519_public_key_id(key.public_key()),
        audience="other-receiver",
        public_key=key.public_key(),
    )
    with pytest.raises(CapabilityGrantDenied, match="audience_mismatch"):
        wrong_audience.verify(
            token,
            request_payload=request_payload,
            transport=transport,
            headers=headers,
            now=NOW,
        )

    wrong_issuer = CapabilityGrantVerifier(
        issuer="other.authority",
        key_id=ed25519_public_key_id(key.public_key()),
        audience=AUDIENCE,
        public_key=key.public_key(),
    )
    with pytest.raises(CapabilityGrantDenied, match="issuer_mismatch"):
        wrong_issuer.verify(
            token,
            request_payload=request_payload,
            transport=transport,
            headers=headers,
            now=NOW,
        )

    wrong_key_id = CapabilityGrantVerifier(
        issuer=ISSUER,
        key_id="ed25519:" + ("0" * 32),
        audience=AUDIENCE,
        public_key=key.public_key(),
    )
    with pytest.raises(CapabilityGrantDenied, match="key_mismatch"):
        wrong_key_id.verify(
            token,
            request_payload=request_payload,
            transport=transport,
            headers=headers,
            now=NOW,
        )

    wrong_public_key = CapabilityGrantVerifier(
        issuer=ISSUER,
        key_id=ed25519_public_key_id(key.public_key()),
        audience=AUDIENCE,
        public_key=Ed25519PrivateKey.generate().public_key(),
    )
    with pytest.raises(CapabilityGrantDenied, match="signature_invalid"):
        wrong_public_key.verify(
            token,
            request_payload=request_payload,
            transport=transport,
            headers=headers,
            now=NOW,
        )


def test_request_transport_path_and_context_headers_are_exact() -> None:
    _, verifier, token, headers, request_payload, transport = _grant_bundle()
    with pytest.raises(CapabilityGrantDenied, match="request_mismatch"):
        verifier.verify(
            token,
            request_payload={"query": "different"},
            transport=transport,
            headers=headers,
            now=NOW,
        )
    with pytest.raises(CapabilityGrantDenied, match="transport_mismatch"):
        verifier.verify(
            token,
            request_payload=request_payload,
            transport=transport.model_copy(update={"path": "/v1/other"}),
            headers=headers,
            now=NOW,
        )
    for header_name in (GRANT_ID_HEADER, TASK_ID_HEADER, TRACE_ID_HEADER, ACTION_ID_HEADER):
        changed = {**headers, header_name: "wrong"}
        with pytest.raises(CapabilityGrantDenied, match="bound_header_mismatch"):
            verifier.verify(
                token,
                request_payload=request_payload,
                transport=transport,
                headers=changed,
                now=NOW,
            )


@pytest.mark.parametrize(
    ("overrides", "now", "error"),
    [
        ({"expires_at": NOW}, NOW, "expired"),
        ({"issued_at": NOW + 10.0, "not_before": NOW + 11.0}, NOW, "not_yet_valid"),
        ({"not_before": NOW - 20.0, "issued_at": NOW - 10.0}, NOW, "time_bounds_invalid"),
    ],
)
def test_time_bounds_fail_closed(overrides: Mapping[str, Any], now: float, error: str) -> None:
    _, verifier, token, headers, request_payload, transport = _grant_bundle(overrides=overrides)
    with pytest.raises(CapabilityGrantError, match=error):
        verifier.verify(token, request_payload=request_payload, transport=transport, headers=headers, now=now)


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": "capability-grant-v2"},
        {"max_uses": 2},
        {"signature_algorithm": "HS256"},
    ],
)
def test_schema_single_use_and_algorithm_are_mandatory(overrides: Mapping[str, Any]) -> None:
    _, verifier, token, headers, request_payload, transport = _grant_bundle(overrides=overrides)
    with pytest.raises(CapabilityGrantFormatError, match="claims_invalid"):
        verifier.verify(token, request_payload=request_payload, transport=transport, headers=headers, now=NOW)


def test_key_id_is_derived_from_raw_public_key() -> None:
    key = Ed25519PrivateKey.generate().public_key()
    assert ed25519_public_key_id(key).startswith("ed25519:")
    assert len(ed25519_public_key_id(key)) == len("ed25519:") + 32


def test_query_params_are_canonical_wire_strings() -> None:
    assert canonical_request_payload(
        method="GET",
        params={"limit": 2, "active": True, "tag": ["a", "b"]},
    ) == {"active": "true", "limit": "2", "tag": ["a", "b"]}


def test_service_token_can_explicitly_accept_internal_token_without_changing_default() -> None:
    verify_service_token(
        service_name="broker",
        configured_key="authority-token",
        x_internal_token="authority-token",
        accept_internal_token=True,
    )
    with pytest.raises(HTTPException) as exc_info:
        verify_service_token(
            service_name="ordinary-service",
            configured_key="authority-token",
            x_internal_token="authority-token",
        )
    assert getattr(exc_info.value, "status_code", None) == 401


def test_internal_service_key_never_inherits_public_service_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "public-service-key")
    monkeypatch.setenv("INTERNAL_API_KEY", "internal-service-key")
    monkeypatch.delenv("INTERNAL_API_KEY_FILE", raising=False)
    monkeypatch.delenv("ORC_INTERNAL_API_KEY_FILE", raising=False)

    assert internal_service_api_key() == "internal-service-key"


class WorkBody(BaseModel):
    query: str
    count: int


def _dependency_app(
    *,
    verifier: CapabilityGrantVerifier,
    redeemer: Any,
    redeem_auth_headers: Any = None,
    required_claims: Mapping[str, Any] | None = None,
) -> tuple[FastAPI, dict[str, int]]:
    app = FastAPI()
    handler_calls = {"count": 0}
    dependency = capability_grant_dependency(
        verifier=verifier,
        receiver_id="receiver-instance-1",
        transport_type="feature_endpoint",
        service="receiver-service",
        redeemer=redeemer,
        auth_profile="internal_api",
        required_claims=required_claims,
        redeem_auth_headers=redeem_auth_headers,
        now=lambda: NOW,
    )

    @app.post("/v1/work")
    async def work(
        payload: WorkBody = Body(),
        grant: VerifiedCapabilityGrant = Depends(dependency),
    ) -> dict[str, Any]:
        handler_calls["count"] += 1
        return {"grant_id": grant.grant_id, "body": payload.model_dump()}

    return app, handler_calls


def test_dependency_reconstructs_json_body_and_query_before_single_redeem(tmp_path) -> None:
    body = {"query": "hello", "count": 2}
    request_payload = canonical_request_payload(
        method="POST",
        body=body,
        params=[("mode", "fast"), ("tag", "a"), ("tag", "b")],
    )
    _, verifier, token, headers, _, transport = _grant_bundle(
        request_payload=request_payload,
        transport=_transport(),
    )
    secret = tmp_path / "broker-token"
    secret.write_text("authority-token\n", encoding="utf-8")
    calls: list[tuple[CapabilityGrantRedemptionRequest, Mapping[str, str]]] = []

    async def redeem(
        request: CapabilityGrantRedemptionRequest,
        *,
        auth_headers: Mapping[str, str],
    ) -> bool:
        calls.append((request, auth_headers))
        return True

    app, handler_calls = _dependency_app(
        verifier=verifier,
        redeemer=redeem,
        redeem_auth_headers=lambda: broker_internal_token_headers(
            token_file=secret,
            receiver_id="receiver-instance-1",
        ),
    )
    response = TestClient(app).post(
        "/v1/work?mode=fast&tag=a&tag=b",
        json=body,
        headers={GRANT_HEADER: token, **headers},
    )

    assert response.status_code == 200
    assert response.json() == {"grant_id": "grant-1", "body": body}
    assert handler_calls["count"] == 1
    assert len(calls) == 1
    redemption, auth_headers = calls[0]
    assert redemption.request_hash == canonical_sha256(request_payload)
    assert redemption.transport == transport
    assert redemption.token_fingerprint == token_fingerprint(token)
    assert auth_headers == {
        "X-Internal-Token": "authority-token",
        "X-AI-Local-Capability-Receiver": "receiver-instance-1",
    }


def test_dependency_preserves_percent_encoded_raw_path_for_transport_authority() -> None:
    body = {"query": "hello", "count": 2}
    transport = _transport(path="/v1/items/item%3Aone")
    _, verifier, token, headers, _, _ = _grant_bundle(
        request_payload=body,
        transport=transport,
    )
    redemptions: list[CapabilityGrantRedemptionRequest] = []

    async def redeem(
        redemption: CapabilityGrantRedemptionRequest,
        *,
        auth_headers: Mapping[str, str],
    ) -> bool:
        assert auth_headers == {}
        redemptions.append(redemption)
        return True

    app = FastAPI()
    dependency = capability_grant_dependency(
        verifier=verifier,
        receiver_id="receiver-instance-1",
        transport_type="feature_endpoint",
        service="receiver-service",
        redeemer=redeem,
        auth_profile="internal_api",
        now=lambda: NOW,
    )

    @app.post("/v1/items/{item_id}")
    async def work_item(
        item_id: str,
        payload: WorkBody = Body(),
        grant: VerifiedCapabilityGrant = Depends(dependency),
    ) -> dict[str, Any]:
        return {
            "grant_id": grant.grant_id,
            "item_id": item_id,
            "body": payload.model_dump(),
        }

    response = TestClient(app).post(
        "/v1/items/item%3Aone",
        json=body,
        headers={GRANT_HEADER: token, **headers},
    )

    assert response.status_code == 200
    assert response.json()["item_id"] == "item:one"
    assert len(redemptions) == 1
    assert redemptions[0].transport.path == "/v1/items/item%3Aone"


def test_dependency_checks_exact_signed_claims_before_redemption() -> None:
    body = {"query": "hello", "count": 2}
    required_claims = {
        "capability_id": "owner.work",
        "owner": "owner-service",
        "permission_scopes": ["work:read"],
        "allowed_data_scopes": ["task.goal"],
        "allowed_effect": "read_only",
        "operation_classes": ["owner_work"],
    }
    _, verifier, token, headers, _, _ = _grant_bundle(
        request_payload=body,
        overrides=required_claims,
    )
    calls = {"redeem": 0}

    async def redeem(
        _: CapabilityGrantRedemptionRequest,
        *,
        auth_headers: Mapping[str, str],
    ) -> bool:
        assert auth_headers == {}
        calls["redeem"] += 1
        return True

    app, handler_calls = _dependency_app(
        verifier=verifier,
        redeemer=redeem,
        required_claims=required_claims,
    )
    required_claims["capability_id"] = "mutated-after-construction"
    response = TestClient(app).post(
        "/v1/work",
        json=body,
        headers={GRANT_HEADER: token, **headers},
    )

    assert response.status_code == 200
    assert calls["redeem"] == 1
    assert handler_calls["count"] == 1


@pytest.mark.parametrize(
    "signed_claims",
    [
        {"capability_id": "other.work", "permission_scopes": ["work:read"]},
        {"capability_id": "owner.work", "permission_scopes": ["work:read", "work:write"]},
        {"permission_scopes": ["work:read"]},
    ],
)
def test_dependency_rejects_wrong_or_missing_signed_claim_before_redeem(
    signed_claims: dict[str, Any],
) -> None:
    body = {"query": "hello", "count": 2}
    _, verifier, token, headers, _, _ = _grant_bundle(
        request_payload=body,
        overrides=signed_claims,
    )
    calls = {"redeem": 0}

    async def redeem(
        _: CapabilityGrantRedemptionRequest,
        *,
        auth_headers: Mapping[str, str],
    ) -> bool:
        calls["redeem"] += 1
        return True

    app, handler_calls = _dependency_app(
        verifier=verifier,
        redeemer=redeem,
        required_claims={
            "capability_id": "owner.work",
            "permission_scopes": ["work:read"],
        },
    )
    response = TestClient(app).post(
        "/v1/work",
        json=body,
        headers={GRANT_HEADER: token, **headers},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "capability_grant_required_claim_mismatch"
    assert calls["redeem"] == 0
    assert handler_calls["count"] == 0


def test_dependency_rejects_non_json_required_claim_configuration() -> None:
    _, verifier, _, _, _, _ = _grant_bundle()

    async def redeem(
        _: CapabilityGrantRedemptionRequest,
        *,
        auth_headers: Mapping[str, str],
    ) -> bool:
        return True

    with pytest.raises(
        CapabilityGrantFormatError,
        match="capability_grant_required_claims_invalid",
    ):
        capability_grant_dependency(
            verifier=verifier,
            receiver_id="receiver-instance-1",
            transport_type="feature_endpoint",
            service="receiver-service",
            redeemer=redeem,
            required_claims={"capability_id": object()},  # type: ignore[dict-item]
        )


def test_dependency_redeem_denial_never_calls_handler() -> None:
    body = {"query": "hello", "count": 2}
    _, verifier, token, headers, _, _ = _grant_bundle(request_payload=body)
    calls = {"redeem": 0}

    async def deny(_: CapabilityGrantRedemptionRequest, *, auth_headers: Mapping[str, str]) -> bool:
        assert auth_headers == {}
        calls["redeem"] += 1
        return False

    app, handler_calls = _dependency_app(verifier=verifier, redeemer=deny)
    response = TestClient(app).post("/v1/work", json=body, headers={GRANT_HEADER: token, **headers})

    assert response.status_code == 403
    assert response.json()["detail"] == "capability_grant_redeem_denied"
    assert calls["redeem"] == 1
    assert handler_calls["count"] == 0


def test_dependency_reconstructs_get_query_strings() -> None:
    request_payload = canonical_request_payload(
        method="GET",
        params=[("q", "hello"), ("tag", "a"), ("tag", "b")],
    )
    _, verifier, token, headers, _, _ = _grant_bundle(
        request_payload=request_payload,
        transport=_transport(method="GET", path="/v1/lookup"),
    )
    calls: list[CapabilityGrantRedemptionRequest] = []

    async def redeem(
        redemption: CapabilityGrantRedemptionRequest,
        *,
        auth_headers: Mapping[str, str],
    ) -> bool:
        assert auth_headers == {}
        calls.append(redemption)
        return True

    app = FastAPI()
    dependency = capability_grant_dependency(
        verifier=verifier,
        receiver_id="receiver-instance-1",
        transport_type="feature_endpoint",
        service="receiver-service",
        redeemer=redeem,
        auth_profile="internal_api",
        now=lambda: NOW,
    )

    @app.get("/v1/lookup")
    async def lookup(grant: VerifiedCapabilityGrant = Depends(dependency)) -> dict[str, str]:
        return {"grant_id": grant.grant_id}

    response = TestClient(app).get(
        "/v1/lookup?q=hello&tag=a&tag=b",
        headers={GRANT_HEADER: token, **headers},
    )
    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0].request_hash == canonical_sha256(request_payload)


def test_dependency_redeem_unavailable_fails_closed_without_handler() -> None:
    body = {"query": "hello", "count": 2}
    _, verifier, token, headers, _, _ = _grant_bundle(request_payload=body)

    async def unavailable(_: CapabilityGrantRedemptionRequest, *, auth_headers: Mapping[str, str]) -> bool:
        raise httpx.ConnectError("authority offline")

    app, handler_calls = _dependency_app(verifier=verifier, redeemer=unavailable)
    response = TestClient(app).post("/v1/work", json=body, headers={GRANT_HEADER: token, **headers})

    assert response.status_code == 503
    assert response.json()["detail"] == "capability_grant_redeem_unavailable"
    assert handler_calls["count"] == 0


@pytest.mark.parametrize("duplicated_header", [GRANT_HEADER, TASK_ID_HEADER])
def test_dependency_rejects_duplicate_authority_header_before_redeem(duplicated_header: str) -> None:
    body = {"query": "hello", "count": 2}
    _, verifier, token, headers, _, _ = _grant_bundle(request_payload=body)
    calls = {"redeem": 0}

    async def redeem(_: CapabilityGrantRedemptionRequest, *, auth_headers: Mapping[str, str]) -> bool:
        calls["redeem"] += 1
        return True

    app, handler_calls = _dependency_app(verifier=verifier, redeemer=redeem)
    all_headers = {GRANT_HEADER: token, **headers}
    value = all_headers.pop(duplicated_header)
    duplicated = httpx.Headers([(duplicated_header, value), (duplicated_header, value), *all_headers.items()])
    response = TestClient(app).post("/v1/work", json=body, headers=duplicated)

    assert response.status_code == 401
    assert response.json()["detail"] == "capability_grant_header_duplicated"
    assert calls["redeem"] == 0
    assert handler_calls["count"] == 0


def test_dependency_rejects_duplicate_json_before_redeem() -> None:
    body = {"query": "hello", "count": 2}
    _, verifier, token, headers, _, _ = _grant_bundle(request_payload=body)
    calls = {"redeem": 0}

    async def redeem(_: CapabilityGrantRedemptionRequest, *, auth_headers: Mapping[str, str]) -> bool:
        calls["redeem"] += 1
        return True

    app, handler_calls = _dependency_app(verifier=verifier, redeemer=redeem)
    response = TestClient(app).post(
        "/v1/work",
        content='{"query":"hello","query":"shadow","count":2}',
        headers={"Content-Type": "application/json", GRANT_HEADER: token, **headers},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "json_duplicate_object_key"
    assert calls["redeem"] == 0
    assert handler_calls["count"] == 0


@pytest.mark.asyncio
async def test_http_redeemer_posts_exact_payload_and_requires_exact_success() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "redeemed": True,
                "grant_id": payload["grant_id"],
                "event_id": "event-redeemed-1",
            },
        )

    transport = _transport()
    redemption = CapabilityGrantRedemptionRequest(
        grant_id="grant/one",
        grant_hash="1" * 64,
        token_fingerprint="2" * 64,
        receiver_id="receiver-1",
        audience=AUDIENCE,
        request_hash="3" * 64,
        transport=transport,
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    redeemer = AsyncHTTPCapabilityGrantRedeemer(
        broker_base_url="https://broker.local",
        endpoint_template="/grants/{grant_id}/redeem",
        client=client,
    )
    try:
        assert await redeemer(redemption, auth_headers={"X-Internal-Token": "secret"}) is True
        assert seen[0].url.raw_path == b"/grants/grant%2Fone/redeem"
        assert seen[0].headers["X-Internal-Token"] == "secret"
        assert json.loads(seen[0].content) == redemption.model_dump(mode="json")
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (403, {"redeemed": False}, False),
        (200, {"redeemed": False, "grant_id": "grant-1"}, False),
        (200, {"redeemed": True, "grant_id": "other"}, False),
        (200, {"redeemed": True, "grant_id": "grant-1"}, False),
        (200, {"redeemed": True, "grant_id": "grant-1", "event_id": ""}, False),
    ],
)
async def test_http_redeemer_denies_4xx_or_inexact_success(status: int, payload: Any, expected: bool) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    redemption = CapabilityGrantRedemptionRequest(
        grant_id="grant-1",
        grant_hash="1" * 64,
        token_fingerprint="2" * 64,
        receiver_id="receiver-1",
        audience=AUDIENCE,
        request_hash="3" * 64,
        transport=_transport(),
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    redeemer = AsyncHTTPCapabilityGrantRedeemer(
        broker_base_url="https://broker.local",
        endpoint_template="/redeem",
        client=client,
    )
    try:
        assert await redeemer(redemption, auth_headers={}) is expected
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["server_error", "transport_error"])
async def test_http_redeemer_fails_closed_on_unavailable_authority(mode: str) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if mode == "transport_error":
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(503, json={"detail": "unavailable"})

    redemption = CapabilityGrantRedemptionRequest(
        grant_id="grant-1",
        grant_hash="1" * 64,
        token_fingerprint="2" * 64,
        receiver_id="receiver-1",
        audience=AUDIENCE,
        request_hash="3" * 64,
        transport=_transport(),
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    redeemer = AsyncHTTPCapabilityGrantRedeemer(
        broker_base_url="https://broker.local",
        endpoint_template="/redeem",
        client=client,
    )
    try:
        with pytest.raises(CapabilityGrantRedeemUnavailable):
            await redeemer(redemption, auth_headers={})
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "base_url",
    ["http://broker.local", "https://user:password@broker.local"],
)
def test_http_redeemer_rejects_insecure_or_credentialed_authority_url(base_url: str) -> None:
    with pytest.raises(CapabilityGrantFormatError, match="broker_base_url_invalid"):
        AsyncHTTPCapabilityGrantRedeemer(
            broker_base_url=base_url,
            endpoint_template="/redeem",
        )
