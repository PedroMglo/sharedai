"""Pydantic schemas for ``resource-governor.v1``.

The contract is intentionally small and explicit.  Heavy local AI services can
share these types without importing orchestrator internals.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sharedai.system.resource_governor.constants import (
    CONTRACT_VERSION,
    DEFAULT_ACTIVITY_TTL_SECONDS,
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    DEFAULT_LEASE_TTL_SECONDS,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_lease_id() -> str:
    return f"lease_{uuid4().hex}"


def new_activity_id() -> str:
    return f"activity_{uuid4().hex}"


class ContractModel(BaseModel):
    model_config = ConfigDict(use_enum_values=True, extra="forbid")


class GovernorMode(StrEnum):
    OBSERVE_ONLY = "observe_only"
    ADVISORY = "advisory"
    ENFORCED = "enforced"
    STRICT = "strict"


class Lane(StrEnum):
    INTERACTIVE = "interactive"
    INTERACTIVE_ENRICHMENT = "interactive_enrichment"
    BACKGROUND = "background"
    STORAGE = "storage"
    HEAVY_GPU = "heavy_gpu"


class LeaseScope(StrEnum):
    REQUEST = "request"
    SESSION = "session"
    BATCH = "batch"
    ARCHIVE = "archive"
    MODEL_LOAD = "model_load"
    BACKGROUND_CYCLE = "background_cycle"


class ResourceClass(StrEnum):
    CPU = "cpu"
    RAM = "ram"
    VRAM = "vram"
    IO_READ = "io_read"
    IO_WRITE = "io_write"
    QDRANT_WRITE = "qdrant_write"
    MODEL_RUNTIME = "model_runtime"


class Capability(StrEnum):
    CHAT_STREAM = "chat_stream"
    ROUTING = "routing"
    RERANK = "rerank"
    RAG_QUERY = "rag_query"
    DOCUMENT_ETL = "document_etl"
    EMBEDDING_GPU_BATCH = "embedding_gpu_batch"
    EMBEDDING_CPU_BATCH = "embedding_cpu_batch"
    GRAPH_LLM = "graph_llm"
    BM25_REBUILD = "bm25_rebuild"
    AUDIO_TRANSCRIBE_GPU = "audio_transcribe_gpu"
    AUDIO_TRANSCRIBE_CPU = "audio_transcribe_cpu"
    STORAGE_ARCHIVE = "storage_archive"
    MODEL_WARMUP = "model_warmup"
    MODEL_LOAD = "model_load"
    DEEP_REASONING_BATCH = "deep_reasoning_batch"


class QualityPolicy(StrEnum):
    PRESERVE = "preserve"
    DEGRADE_ALLOWED = "degrade_allowed"
    SKIP_ALLOWED = "skip_allowed"


class QualityImpact(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class LeaseDecisionKind(StrEnum):
    GRANTED = "granted"
    GRANTED_WITH_LIMITS = "granted_with_limits"
    DEFER = "defer"
    DENY = "deny"
    RUN_CPU_ONLY = "run_cpu_only"
    SKIP_OPTIONAL = "skip_optional"


class DecisionType(StrEnum):
    NORMAL = "normal"
    SOFT_ADVICE = "soft_advice"
    HARD_BLOCK = "hard_block"


class UserImpact(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ActivityType(StrEnum):
    INTERACTIVE_CHAT_STREAM = "interactive_chat_stream"
    INTERACTIVE_QUERY = "interactive_query"
    AUDIO_JOB = "audio_job"
    BACKGROUND_JOB = "background_job"


class PressureLevel(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class LeaseRequest(ContractModel):
    contract_version: str = CONTRACT_VERSION
    idempotency_key: str

    requester: str
    component: str
    lane: Lane
    lease_scope: LeaseScope
    resource_class: ResourceClass
    capability: Capability

    estimated_duration_seconds: int | None = None
    estimated_ram_mb: int | None = None
    estimated_vram_mb: int | None = None
    estimated_io_mb: int | None = None
    requested_ttl_seconds: int | None = None

    preemptible: bool
    quality_policy: QualityPolicy
    estimated_quality_impact: QualityImpact

    request_id: str
    session_id: str | None = None

    @field_validator("contract_version")
    @classmethod
    def _contract_version_must_match(cls, value: str) -> str:
        if value != CONTRACT_VERSION:
            raise ValueError(f"unsupported contract_version: {value}")
        return value

    @field_validator("idempotency_key", "requester", "component", "request_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be blank")
        return value


class LeaseDecision(ContractModel):
    contract_version: str = CONTRACT_VERSION

    decision: LeaseDecisionKind
    decision_type: DecisionType = DecisionType.NORMAL

    lease_id: str | None = None
    ttl_seconds: int | None = None
    heartbeat_interval_seconds: int | None = None

    limits: dict[str, Any] = Field(default_factory=dict)
    reason: str
    retry_after_seconds: int | None = None

    effective_quality_policy: str = QualityPolicy.PRESERVE
    expected_user_impact: UserImpact = UserImpact.NONE

    @property
    def granted(self) -> bool:
        decision = LeaseDecisionKind(self.decision)
        return decision in {
            LeaseDecisionKind.GRANTED,
            LeaseDecisionKind.GRANTED_WITH_LIMITS,
            LeaseDecisionKind.RUN_CPU_ONLY,
        }

    @field_validator("contract_version")
    @classmethod
    def _contract_version_must_match(cls, value: str) -> str:
        if value != CONTRACT_VERSION:
            raise ValueError(f"unsupported contract_version: {value}")
        return value


class LeaseHeartbeat(ContractModel):
    contract_version: str = CONTRACT_VERSION
    lease_id: str
    owner: str
    request_id: str


class LeaseRecord(ContractModel):
    contract_version: str = CONTRACT_VERSION
    lease_id: str = Field(default_factory=new_lease_id)
    request: LeaseRequest
    decision: LeaseDecision
    owner: str
    created_at: datetime = Field(default_factory=utc_now)
    last_heartbeat_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    released_at: datetime | None = None

    @classmethod
    def from_request(cls, request: LeaseRequest, decision: LeaseDecision) -> "LeaseRecord":
        now = utc_now()
        ttl = decision.ttl_seconds or request.requested_ttl_seconds or DEFAULT_LEASE_TTL_SECONDS
        lease_id = decision.lease_id or new_lease_id()
        decision.lease_id = lease_id
        decision.ttl_seconds = ttl
        if decision.heartbeat_interval_seconds is None:
            decision.heartbeat_interval_seconds = min(DEFAULT_HEARTBEAT_INTERVAL_SECONDS, max(1, ttl // 3))
        return cls(
            lease_id=lease_id,
            request=request,
            decision=decision,
            owner=request.requester,
            created_at=now,
            last_heartbeat_at=now,
            expires_at=now + timedelta(seconds=ttl),
        )

    def heartbeat(self, ttl_seconds: int | None = None) -> None:
        now = utc_now()
        ttl = ttl_seconds or self.decision.ttl_seconds or DEFAULT_LEASE_TTL_SECONDS
        self.last_heartbeat_at = now
        self.expires_at = now + timedelta(seconds=ttl)


class ActivityRequest(ContractModel):
    contract_version: str = CONTRACT_VERSION
    idempotency_key: str
    activity_type: ActivityType
    requester: str = "orchestrator"
    capability: Capability = Capability.CHAT_STREAM
    request_id: str
    session_id: str | None = None
    ttl_seconds: int = DEFAULT_ACTIVITY_TTL_SECONDS
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActivityRecord(ContractModel):
    contract_version: str = CONTRACT_VERSION
    activity_id: str = Field(default_factory=new_activity_id)
    request: ActivityRequest
    created_at: datetime = Field(default_factory=utc_now)
    last_heartbeat_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    released_at: datetime | None = None

    @classmethod
    def from_request(cls, request: ActivityRequest) -> "ActivityRecord":
        now = utc_now()
        return cls(
            request=request,
            created_at=now,
            last_heartbeat_at=now,
            expires_at=now + timedelta(seconds=request.ttl_seconds),
        )

    def heartbeat(self) -> None:
        now = utc_now()
        self.last_heartbeat_at = now
        self.expires_at = now + timedelta(seconds=self.request.ttl_seconds)


class ResourceSnapshot(ContractModel):
    contract_version: str = CONTRACT_VERSION
    sampled_at: datetime = Field(default_factory=utc_now)
    cpu_percent: float | None = None
    ram_total_mb: int | None = None
    ram_available_mb: int | None = None
    ram_percent: float | None = None
    swap_used_mb: int | None = None
    swap_percent: float | None = None
    swap_growth_mb: int | None = None
    disk_free_mb: int | None = None
    disk_percent: float | None = None
    disk_free_ratio: float | None = None
    psi_cpu_some: float | None = None
    psi_memory_some: float | None = None
    psi_io_some: float | None = None
    gpu_available: bool = False
    vram_total_mb: int | None = None
    vram_used_mb: int | None = None
    vram_free_mb: int | None = None
    gpu_utilization_pct: float | None = None
    battery_percent: float | None = None
    battery_power_plugged: bool | None = None
    thermal_max_celsius: float | None = None
    thermal_throttle: bool = False
    lid_closed: bool | None = None
    pressure_level: PressureLevel = PressureLevel.LOW
    pressure_reasons: list[str] = Field(default_factory=list)
    active_activities: int = 0
    active_leases: int = 0


class EffectivePolicy(ContractModel):
    model_config = ConfigDict(use_enum_values=True, extra="allow")

    contract_version: str = CONTRACT_VERSION
    generated_at: datetime = Field(default_factory=utc_now)
    source: str = "smart_resolver"
    mode: GovernorMode = GovernorMode.OBSERVE_ONLY
    machine_profile: str = "balanced_desktop"
    thin_but_capable: bool = False
    foreground_first: bool = True
    preserve_quality: bool = True
    allow_deferred_quality: bool = True
    allow_silent_quality_loss: bool = False
    lanes: dict[str, Any] = Field(default_factory=dict)
    thresholds: dict[str, Any] = Field(default_factory=dict)
    slo_budget: dict[str, Any] = Field(default_factory=dict)
    limits: dict[str, Any] = Field(default_factory=dict)
    resource_classes: dict[str, Any] = Field(default_factory=dict)
    gpu_conflict_matrix: dict[str, dict[str, list[str]]] = Field(default_factory=dict)
    fallback_policy: dict[str, Any] = Field(default_factory=dict)
    runtime: dict[str, Any] = Field(default_factory=dict)


class GovernorMetrics(ContractModel):
    contract_version: str = CONTRACT_VERSION
    decisions_total: int = 0
    grants_total: int = 0
    defers_total: int = 0
    denies_total: int = 0
    soft_advice_total: int = 0
    hard_blocks_total: int = 0
    expired_leases_total: int = 0
    expired_activities_total: int = 0
    active_leases: int = 0
    active_activities: int = 0
