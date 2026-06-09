"""Small stdlib HTTPS client for the authoritative Resource Governor."""

from __future__ import annotations

import json
import os
import ssl
import time
from contextlib import contextmanager
from typing import Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from sharedai.system.resource_governor.constants import (
    DEFAULT_CLIENT_TIMEOUT_SECONDS,
    DEFAULT_GOVERNOR_URL,
)
from sharedai.system.resource_governor.fallback import fallback_decision
from sharedai.system.resource_governor.schemas import (
    ActivityRecord,
    ActivityRequest,
    EffectivePolicy,
    GovernorMetrics,
    LeaseDecision,
    LeaseRequest,
    ResourceSnapshot,
)


def _read_token_file() -> str:
    for env_name in ("AI_RESOURCE_GOVERNOR_TOKEN_FILE", "INTERNAL_API_KEY_FILE", "ORC_INTERNAL_API_KEY_FILE"):
        path = os.environ.get(env_name)
        if not path:
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            continue
    return ""


class ResourceGovernorClient:
    """HTTPS client with conservative fallback semantics."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout_seconds: float | None = None,
        fallback_enabled: bool = True,
    ) -> None:
        self.base_url = (base_url or os.environ.get("AI_RESOURCE_GOVERNOR_URL") or DEFAULT_GOVERNOR_URL).rstrip("/")
        self.token = token if token is not None else (os.environ.get("AI_RESOURCE_GOVERNOR_TOKEN", "") or _read_token_file())
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else float(os.environ.get("AI_RESOURCE_GOVERNOR_TIMEOUT_SECONDS", DEFAULT_CLIENT_TIMEOUT_SECONDS))
        )
        self.fallback_enabled = fallback_enabled
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Resource Governor URL must use https")

    def _tls_context(self) -> ssl.SSLContext | None:
        verify = os.environ.get("AI_RESOURCE_GOVERNOR_TLS_VERIFY", "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if verify:
            return None
        return ssl._create_unverified_context()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request_json(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = None if payload is None else json.dumps(payload, default=str).encode("utf-8")
        req = Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers=self._headers(),
        )
        with urlopen(req, timeout=self.timeout_seconds, context=self._tls_context()) as response:  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            raw = response.read()
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def request_lease(self, request: LeaseRequest) -> LeaseDecision:
        try:
            payload = self._request_json("POST", "/resources/leases", request.model_dump(mode="json"))
            return LeaseDecision.model_validate(payload)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            if not self.fallback_enabled:
                raise
            return fallback_decision(request, reason=str(exc))

    def heartbeat(self, lease_id: str, *, owner: str = "", request_id: str = "") -> bool:
        if lease_id.startswith("local_lease_"):
            return True
        try:
            self._request_json(
                "POST",
                f"/resources/leases/{lease_id}/heartbeat",
                {"lease_id": lease_id, "owner": owner, "request_id": request_id},
            )
            return True
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            return False

    def release(self, lease_id: str) -> bool:
        if lease_id.startswith("local_lease_"):
            return True
        try:
            self._request_json("DELETE", f"/resources/leases/{lease_id}")
            return True
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            return False

    def register_activity(self, request: ActivityRequest) -> ActivityRecord | None:
        try:
            payload = self._request_json("POST", "/resources/activity", request.model_dump(mode="json"))
            return ActivityRecord.model_validate(payload)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            return None

    def snapshot(self) -> ResourceSnapshot | None:
        try:
            return ResourceSnapshot.model_validate(self._request_json("GET", "/resources/snapshot"))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            return None

    def effective_policy(self) -> EffectivePolicy | None:
        try:
            return EffectivePolicy.model_validate(self._request_json("GET", "/resources/effective-policy"))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            return None

    def metrics(self) -> GovernorMetrics | None:
        try:
            return GovernorMetrics.model_validate(self._request_json("GET", "/resources/metrics"))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            return None


@contextmanager
def lease_context(client: ResourceGovernorClient, request: LeaseRequest) -> Iterator[LeaseDecision]:
    """Request a lease and release it at the end of the context."""
    decision = client.request_lease(request)
    last_heartbeat = time.monotonic()
    try:
        yield decision
        if decision.lease_id and decision.heartbeat_interval_seconds:
            now = time.monotonic()
            if now - last_heartbeat >= decision.heartbeat_interval_seconds:
                client.heartbeat(decision.lease_id, owner=request.requester, request_id=request.request_id)
                last_heartbeat = now
    finally:
        if decision.lease_id:
            client.release(decision.lease_id)
