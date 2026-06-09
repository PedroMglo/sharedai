"""Shared storage guardian object contracts.

These models define the v1 object-write contract used by services that need
managed storage without receiving filesystem write permissions.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

STORAGE_CONTRACT_VERSION = "1.0.0"
DEFAULT_MAX_INLINE_BYTES = 256 * 1024
DEFAULT_UPLOAD_TTL_SECONDS = 900

_SHA256_RE = re.compile(r"^(sha256:)?[0-9a-fA-F]{64}$")


def utc_now() -> datetime:
    return datetime.now(UTC)


class StorageContractModel(BaseModel):
    model_config = ConfigDict(use_enum_values=True, extra="forbid")


class StorageObjectStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    SOFT_DELETED = "soft_deleted"
    QUARANTINED = "quarantined"
    EXPIRED = "expired"
    PURGED = "purged"


class StorageUploadStatus(StrEnum):
    UPLOADING = "uploading"
    COMMITTED = "committed"
    EXPIRED = "expired"
    QUARANTINED = "quarantined"


class StorageAuthorityMetadata(StorageContractModel):
    storage_authority: str = "storage_guardian"
    authority_required: bool = True
    actor: str | None = None
    agent_id: str | None = None
    component: str | None = None
    operation: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    trace_id: str | None = None
    task_id: str | None = None
    session_id: str | None = None


class StorageObjectCreate(StorageContractModel):
    agent: str
    logical_name: str = Field(min_length=1, max_length=512)
    content_base64: str = Field(min_length=1)
    store: str | None = None
    zone: str = "ingest"
    content_type: str = "application/octet-stream"
    sha256: str | None = None
    parent_object_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    authority: StorageAuthorityMetadata | None = None

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _SHA256_RE.match(value):
            raise ValueError("sha256 must be a 64 character hex digest, optionally prefixed with sha256:")
        digest = value.split(":", 1)[-1].lower()
        return f"sha256:{digest}"


class StorageUploadSessionCreate(StorageContractModel):
    agent: str
    logical_name: str = Field(min_length=1, max_length=512)
    expected_size: int = Field(ge=0)
    sha256: str
    store: str | None = None
    zone: str = "ingest"
    content_type: str = "application/octet-stream"
    ttl_seconds: int = Field(default=DEFAULT_UPLOAD_TTL_SECONDS, ge=1, le=86_400)
    metadata: dict[str, Any] = Field(default_factory=dict)
    authority: StorageAuthorityMetadata | None = None

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not _SHA256_RE.match(value):
            raise ValueError("sha256 must be a 64 character hex digest, optionally prefixed with sha256:")
        digest = value.split(":", 1)[-1].lower()
        return f"sha256:{digest}"


class StorageUploadCommit(StorageContractModel):
    sha256: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _SHA256_RE.match(value):
            raise ValueError("sha256 must be a 64 character hex digest, optionally prefixed with sha256:")
        digest = value.split(":", 1)[-1].lower()
        return f"sha256:{digest}"


class StorageVersion(StorageContractModel):
    object_id: str
    version_id: str
    store: str
    zone: str
    status: StorageObjectStatus
    logical_name: str
    content_type: str
    size_bytes: int
    sha256: str
    created_by: str
    created_at: float
    parent_object_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class StorageObject(StorageContractModel):
    object_id: str
    latest_version_id: str
    store: str
    zone: str
    status: StorageObjectStatus
    logical_name: str
    content_type: str
    size_bytes: int
    sha256: str
    created_by: str
    created_at: float
    updated_at: float
    parent_object_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class StorageUploadSession(StorageContractModel):
    upload_id: str
    store: str
    zone: str
    status: StorageUploadStatus
    logical_name: str
    expected_size: int
    received_size: int
    sha256: str
    content_type: str
    expires_at: float
    created_by: str
    object_id: str | None = None
    version_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class StorageError(StorageContractModel):
    allowed: bool = False
    reason: str
    detail: str | None = None
    request_id: str | None = None


def storage_contract_schema() -> dict[str, Any]:
    return {
        "storage_contract_version": STORAGE_CONTRACT_VERSION,
        "models": {
            "StorageAuthorityMetadata": StorageAuthorityMetadata.model_json_schema(),
            "StorageObjectCreate": StorageObjectCreate.model_json_schema(),
            "StorageUploadSessionCreate": StorageUploadSessionCreate.model_json_schema(),
            "StorageUploadCommit": StorageUploadCommit.model_json_schema(),
            "StorageObject": StorageObject.model_json_schema(),
            "StorageVersion": StorageVersion.model_json_schema(),
            "StorageUploadSession": StorageUploadSession.model_json_schema(),
            "StorageError": StorageError.model_json_schema(),
        },
    }


def storage_contract_schema_hash() -> str:
    payload = json.dumps(storage_contract_schema(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
