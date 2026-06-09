"""HTTPS client for the storage_guardian object-write contract."""

from __future__ import annotations

import base64
import http.client
import json
import ssl
from typing import Any
from urllib.parse import urlsplit

from sharedai.storage.contracts import (
    StorageObject,
    StorageObjectCreate,
    StorageUploadCommit,
    StorageUploadSession,
    StorageUploadSessionCreate,
)


class StorageClientError(RuntimeError):
    """Raised when storage_guardian rejects a request or cannot be reached."""

    def __init__(self, message: str, *, status_code: int | None = None, reason: str | None = None, detail: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason
        self.detail = detail


class StorageClient:
    """Small dependency-free client for object and upload operations."""

    def __init__(self, base_url: str, *, internal_token: str, timeout_seconds: float = 10.0) -> None:
        if not internal_token:
            raise ValueError("internal_token is required")
        parsed = urlsplit(base_url.rstrip("/"))
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("base_url must be an absolute https URL")
        self.base_url = base_url.rstrip("/")
        self._netloc = parsed.netloc
        self._base_path = parsed.path.rstrip("/")
        self.internal_token = internal_token
        self.timeout_seconds = timeout_seconds

    def storage_schema(self) -> dict[str, Any]:
        return self._request_json("GET", "/storage/schema")

    def list_objects(self, *, agent: str | None = None, zone: str | None = None, status: str | None = None) -> list[StorageObject]:
        query = []
        if agent:
            query.append(("agent", agent))
        if zone:
            query.append(("zone", zone))
        if status:
            query.append(("status", status))
        suffix = ""
        if query:
            from urllib.parse import urlencode

            suffix = "?" + urlencode(query)
        payload = self._request_json("GET", f"/storage/objects{suffix}")
        return [StorageObject.model_validate(item) for item in payload]

    def get_object(self, object_id: str) -> StorageObject:
        payload = self._request_json("GET", f"/storage/objects/{object_id}")
        return StorageObject.model_validate(payload)

    def create_object(self, payload: StorageObjectCreate | dict[str, Any], *, idempotency_key: str) -> StorageObject:
        response = self._request_json(
            "POST",
            "/internal/storage/objects",
            json_body=self._model_payload(payload),
            headers={"Idempotency-Key": idempotency_key},
        )
        return StorageObject.model_validate(response)

    def create_object_bytes(
        self,
        *,
        agent: str,
        logical_name: str,
        content: bytes,
        idempotency_key: str,
        store: str | None = None,
        zone: str = "ingest",
        content_type: str = "application/octet-stream",
        sha256: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StorageObject:
        request = StorageObjectCreate(
            agent=agent,
            store=store,
            zone=zone,
            logical_name=logical_name,
            content_base64=base64.b64encode(content).decode("ascii"),
            content_type=content_type,
            sha256=sha256,
            metadata=metadata or {},
        )
        return self.create_object(request, idempotency_key=idempotency_key)

    def create_upload_session(self, payload: StorageUploadSessionCreate | dict[str, Any]) -> StorageUploadSession:
        response = self._request_json("POST", "/internal/storage/uploads", json_body=self._model_payload(payload))
        return StorageUploadSession.model_validate(response)

    def append_upload_bytes(self, upload_id: str, content: bytes) -> StorageUploadSession:
        response = self._request_json(
            "PUT",
            f"/internal/storage/uploads/{upload_id}",
            body=content,
            content_type="application/octet-stream",
        )
        return StorageUploadSession.model_validate(response)

    def commit_upload(
        self,
        upload_id: str,
        payload: StorageUploadCommit | dict[str, Any],
        *,
        idempotency_key: str,
    ) -> StorageObject:
        response = self._request_json(
            "POST",
            f"/internal/storage/uploads/{upload_id}/commit",
            json_body=self._model_payload(payload),
            headers={"Idempotency-Key": idempotency_key},
        )
        return StorageObject.model_validate(response)

    @staticmethod
    def _model_payload(payload: Any) -> dict[str, Any]:
        if hasattr(payload, "model_dump"):
            return payload.model_dump(exclude_none=True)
        return dict(payload)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        body: bytes | None = None,
        content_type: str = "application/json",
        headers: dict[str, str] | None = None,
    ) -> Any:
        request_headers = {"X-Internal-Token": self.internal_token, **(headers or {})}
        data = body
        if json_body is not None:
            data = json.dumps(json_body, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        elif body is not None:
            request_headers["Content-Type"] = content_type
        request_path = f"{self._base_path}{path}"
        # Verified TLS context is explicit; suppress Semgrep's generic HTTPSConnection audit rule.
        # nosemgrep: python.lang.security.audit.httpsconnection-detected.httpsconnection-detected
        connection = http.client.HTTPSConnection(
            self._netloc,
            timeout=self.timeout_seconds,
            context=ssl.create_default_context(),
        )
        try:
            connection.request(method, request_path, body=data, headers=request_headers)
            response = connection.getresponse()
            raw = response.read()
        except OSError as exc:
            raise StorageClientError(f"storage_guardian unavailable: {exc}") from exc
        finally:
            connection.close()
        if response.status >= 400:
            raise self._http_error(response.status, raw)
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    @staticmethod
    def _http_error(status_code: int, raw: bytes) -> StorageClientError:
        reason = None
        detail = None
        if raw:
            try:
                payload = json.loads(raw.decode("utf-8"))
                reason = payload.get("reason") or payload.get("detail")
                detail = payload.get("detail")
            except (json.JSONDecodeError, UnicodeDecodeError):
                detail = raw.decode("utf-8", errors="replace")
        message = detail or reason or f"storage_guardian returned HTTP {status_code}"
        return StorageClientError(message, status_code=status_code, reason=reason, detail=detail)
