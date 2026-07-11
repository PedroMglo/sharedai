"""Authentication helpers shared by local HTTP services."""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable
from pathlib import Path

from fastapi import Header, HTTPException

DEFAULT_SECRET_FILE = "/run/secrets/internal_api_key"
FILE_ENV_NAMES = ("API_KEY_FILE", "INTERNAL_API_KEY_FILE")
ENV_NAMES = ("API_KEY", "INTERNAL_API_KEY")
INTERNAL_FILE_ENV_NAMES = ("INTERNAL_API_KEY_FILE", "ORC_INTERNAL_API_KEY_FILE")
INTERNAL_ENV_NAMES = ("INTERNAL_API_KEY", "ORC_INTERNAL_API_KEY")


def read_secret_file(path_value: str) -> str:
    if not path_value:
        return ""
    path = Path(path_value)
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def service_api_key(configured_key: str = "") -> str:
    if configured_key.strip():
        return configured_key.strip()
    for file_env in FILE_ENV_NAMES:
        key = read_secret_file(os.environ.get(file_env, ""))
        if key:
            return key
    for env_name in ENV_NAMES:
        key = os.environ.get(env_name, "").strip()
        if key:
            return key
    return read_secret_file(DEFAULT_SECRET_FILE)


def internal_service_api_key(configured_key: str = "") -> str:
    """Resolve only the shared internal credential, never a public service key."""

    if configured_key.strip():
        return configured_key.strip()
    for file_env in INTERNAL_FILE_ENV_NAMES:
        key = read_secret_file(os.environ.get(file_env, ""))
        if key:
            return key
    for env_name in INTERNAL_ENV_NAMES:
        key = os.environ.get(env_name, "").strip()
        if key:
            return key
    return read_secret_file(DEFAULT_SECRET_FILE)


def verify_service_token(
    *,
    service_name: str,
    configured_key: str = "",
    authorization: str | None = None,
    x_api_key: str | None = None,
    x_internal_token: str | None = None,
    accept_internal_token: bool = False,
) -> None:
    expected = service_api_key(configured_key)
    if not expected:
        raise HTTPException(status_code=503, detail=f"{service_name} API key is not configured")

    bearer = ""
    if authorization and authorization.startswith("Bearer "):
        bearer = authorization.removeprefix("Bearer ").strip()
    internal_token = x_internal_token if accept_internal_token else None
    provided = (x_api_key or bearer or internal_token or "").strip()
    if not provided:
        raise HTTPException(status_code=401, detail="Missing API key")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Invalid API key")


def service_token_dependency(
    service_name: str,
    configured_key: Callable[[], str],
    *,
    accept_internal_token: bool = False,
) -> Callable[..., None]:
    """Build a FastAPI dependency that verifies the shared service token."""

    def require_service_token(
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
    ) -> None:
        verify_service_token(
            service_name=service_name,
            configured_key=configured_key(),
            authorization=authorization,
            x_api_key=x_api_key,
            x_internal_token=x_internal_token,
            accept_internal_token=accept_internal_token,
        )

    return require_service_token
