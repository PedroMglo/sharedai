from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sharedai.storage.client import StorageClient, StorageClientError  # noqa: E402


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.status = 200

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _object_payload() -> dict[str, Any]:
    now = time.time()
    return {
        "object_id": "obj_123",
        "latest_version_id": "ver_123",
        "store": "sample",
        "zone": "ingest",
        "status": "active",
        "logical_name": "note.txt",
        "content_type": "text/plain",
        "size_bytes": 4,
        "sha256": "sha256:" + ("0" * 64),
        "created_by": "tests",
        "created_at": now,
        "updated_at": now,
        "metadata": {},
    }


def test_storage_client_create_object_bytes_uses_object_write_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class FakeConnection:
        def __init__(self, netloc: str, timeout: float) -> None:
            seen["netloc"] = netloc
            seen["timeout"] = timeout

        def request(self, method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None) -> None:
            seen["method"] = method
            seen["path"] = path
            seen["token"] = headers["X-Internal-Token"] if headers else None
            seen["idempotency_key"] = headers["Idempotency-Key"] if headers else None
            seen["body"] = json.loads((body or b"{}").decode("utf-8"))

        def getresponse(self) -> _FakeResponse:
            return _FakeResponse(_object_payload())

        def close(self) -> None:
            seen["closed"] = True

    def fake_connection(netloc: str, timeout: float, context: object | None = None) -> FakeConnection:
        return FakeConnection(netloc, timeout)

    monkeypatch.setattr("sharedai.storage.client.http.client.HTTPSConnection", fake_connection)

    client = StorageClient("https://storage.local", internal_token="secret", timeout_seconds=3)
    created = client.create_object_bytes(
        agent="tests",
        store="sample",
        logical_name="note.txt",
        content=b"note",
        content_type="text/plain",
        idempotency_key="idem-1",
        sha256="sha256:" + ("0" * 64),
    )

    assert created.object_id == "obj_123"
    assert seen["netloc"] == "storage.local"
    assert seen["timeout"] == 3
    assert seen["method"] == "POST"
    assert seen["path"] == "/internal/storage/objects"
    assert seen["token"] == "secret"
    assert seen["idempotency_key"] == "idem-1"
    assert seen["body"]["logical_name"] == "note.txt"
    assert seen["body"]["content_base64"] == "bm90ZQ=="
    assert seen["closed"] is True


def test_storage_client_rejects_non_https_base_url() -> None:
    with pytest.raises(ValueError, match="https"):
        StorageClient("file:///tmp/storage", internal_token="secret")
    with pytest.raises(ValueError, match="https"):
        StorageClient("http://storage.local", internal_token="secret")


def test_storage_client_requires_internal_token() -> None:
    with pytest.raises(ValueError, match="internal_token"):
        StorageClient("https://storage.local", internal_token="")


def test_storage_client_wraps_unreachable_guardian(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenConnection:
        def __init__(self, netloc: str, timeout: float) -> None:
            pass

        def request(self, method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None) -> None:
            raise OSError("closed")

        def close(self) -> None:
            return None

    def fake_connection(netloc: str, timeout: float, context: object | None = None) -> BrokenConnection:
        return BrokenConnection(netloc, timeout)

    monkeypatch.setattr("sharedai.storage.client.http.client.HTTPSConnection", fake_connection)
    client = StorageClient("https://storage.local", internal_token="secret")

    with pytest.raises(StorageClientError, match="unavailable"):
        client.storage_schema()


def test_storage_client_preserves_base_path(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class FakeConnection:
        def __init__(self, netloc: str, timeout: float) -> None:
            pass

        def request(self, method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None) -> None:
            seen["path"] = path

        def getresponse(self) -> _FakeResponse:
            return _FakeResponse({})

        def close(self) -> None:
            return None

    def fake_connection(netloc: str, timeout: float, context: object | None = None) -> FakeConnection:
        return FakeConnection(netloc, timeout)

    monkeypatch.setattr("sharedai.storage.client.http.client.HTTPSConnection", fake_connection)
    client = StorageClient("https://storage.local/api", internal_token="secret")

    client.storage_schema()

    assert seen["path"] == "/api/storage/schema"
