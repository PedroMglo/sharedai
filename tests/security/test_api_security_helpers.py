from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sharedai.security.validation import SECURE_HEADERS, redact_mapping, standard_error  # noqa: E402


def test_redact_mapping_removes_secret_values():
    redacted = redact_mapping(
        {
            "Authorization": "Bearer secret",
            "X-API-Key": "abc",
            "nested": {"password": "pw", "safe": "ok"},
            "safe": "visible",
        }
    )

    assert redacted["Authorization"] == "[REDACTED]"
    assert redacted["X-API-Key"] == "[REDACTED]"
    assert redacted["nested"]["password"] == "[REDACTED]"
    assert redacted["nested"]["safe"] == "ok"
    assert redacted["safe"] == "visible"


def test_standard_error_shape_and_headers():
    error = standard_error("RATE_LIMITED", "Too many requests.", "req-1")

    assert error == {
        "error": {
            "code": "RATE_LIMITED",
            "message": "Too many requests.",
            "request_id": "req-1",
        }
    }
    assert SECURE_HEADERS["X-Content-Type-Options"] == "nosniff"
    assert SECURE_HEADERS["X-Frame-Options"] == "DENY"
