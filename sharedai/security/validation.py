"""Shared API security helpers for ai-local services."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

SECRET_HEADER_NAMES = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "x-api-key",
        "cookie",
        "set-cookie",
    }
)

SECRET_FIELD_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "passwd",
    "secret",
    "token",
)

SECURE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cache-Control": "no-store",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def request_id_from_headers(headers: Mapping[str, str] | None = None) -> str:
    """Return a caller-supplied request ID or generate a compact one."""

    if headers:
        for name in ("x-request-id", "X-Request-ID"):
            value = headers.get(name)
            if value:
                return str(value)[:128]
    return uuid.uuid4().hex


def standard_error(code: str, message: str, request_id: str) -> dict[str, dict[str, str]]:
    """Return the standard ai-local error response shape."""

    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
        }
    }


def redact_value(value: Any) -> Any:
    """Replace sensitive scalar values with a stable redaction marker."""

    if value is None:
        return None
    return "[REDACTED]"


def redact_mapping(
    values: Mapping[str, Any],
    *,
    secret_names: set[str] | frozenset[str] = SECRET_HEADER_NAMES,
) -> dict[str, Any]:
    """Redact secret-like keys from headers or structured log fields."""

    redacted: dict[str, Any] = {}
    for key, value in values.items():
        key_str = str(key)
        key_lower = key_str.lower()
        if key_lower in secret_names or any(marker in key_lower for marker in SECRET_FIELD_MARKERS):
            redacted[key_str] = redact_value(value)
        elif isinstance(value, Mapping):
            redacted[key_str] = redact_mapping(value)
        else:
            redacted[key_str] = value
    return redacted
