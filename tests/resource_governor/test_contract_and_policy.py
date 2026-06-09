from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for rel in (".",):
    path = ROOT / rel
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from sharedai.system.resource_governor.effective_policy import build_effective_policy  # noqa: E402
from sharedai.system.resource_governor.fallback import fallback_decision  # noqa: E402
from sharedai.system.resource_governor.schemas import LeaseDecisionKind, LeaseRequest  # noqa: E402


def _lease_request(**overrides) -> LeaseRequest:
    data = {
        "idempotency_key": "test:key",
        "requester": "obsidian-rag",
        "component": "embedding_batcher",
        "lane": "background",
        "lease_scope": "batch",
        "resource_class": "vram",
        "capability": "embedding_gpu_batch",
        "estimated_duration_seconds": 30,
        "estimated_ram_mb": 128,
        "estimated_vram_mb": 512,
        "estimated_io_mb": None,
        "preemptible": True,
        "quality_policy": "preserve",
        "estimated_quality_impact": "high",
        "request_id": "req_1",
        "session_id": None,
    }
    data.update(overrides)
    return LeaseRequest(**data)


def test_lease_request_contract_defaults_version() -> None:
    request = _lease_request()

    assert request.contract_version == "resource-governor.v1"
    assert request.idempotency_key == "test:key"


def test_fallback_pauses_storage_but_allows_interactive() -> None:
    storage = _lease_request(lane="storage", resource_class="io_write", capability="storage_archive")
    interactive = _lease_request(
        lane="interactive",
        lease_scope="request",
        resource_class="model_runtime",
        capability="chat_stream",
        preemptible=False,
        estimated_quality_impact="none",
    )

    assert fallback_decision(storage).decision == LeaseDecisionKind.DEFER
    assert fallback_decision(interactive).decision == LeaseDecisionKind.GRANTED


def test_effective_policy_derives_gpu_8gb_profile() -> None:
    resolved = {
        "config": {"hardware": {"profile": "auto"}},
        "runtime": {
            "cpu_threads": 12,
            "ram_total_gb": 32,
            "gpu_available": True,
            "gpu_name": "RTX 4060",
            "vram_total_gb": 8,
        },
        "decisions": [{"field": "runtime.workers.final", "value": 4}],
    }

    policy = build_effective_policy(resolved_config=resolved)

    assert policy.machine_profile == "gpu_8gb"
    assert policy.mode == "observe_only"
    assert policy.limits["heavy_gpu_concurrency"] == 1
    assert "chat_stream" in policy.gpu_conflict_matrix


def test_effective_policy_treats_local_fallback_as_operational_storage() -> None:
    resolved = {
        "config": {
            "hardware": {"profile": "auto"},
            "storage": {
                "external_root": "/mnt/ai-extreme/ai-local",
                "require_external": True,
                "allow_local_heavy_fallback": True,
            },
        },
        "runtime": {
            "cpu_threads": 12,
            "ram_total_gb": 32,
            "gpu_available": False,
            "storage_exists": False,
            "storage_mounted": False,
            "storage_writable": False,
        },
        "storage_paths": {
            "AI_LOCAL_STORAGE_MODE": "local_fallback",
            "AI_STORAGE_EXTERNAL_ROOT": "/mnt/ai-extreme/ai-local",
        },
        "decisions": [
            {"field": "storage.mode", "value": "local_fallback"},
            {"field": "runtime.workers.final", "value": 2},
        ],
    }

    policy = build_effective_policy(resolved_config=resolved)

    assert policy.storage_policy["fallback_is_operational"] is True
    assert policy.storage_policy["missing_external_is_blocker"] is False
    assert policy.storage_policy["external_configured"] is True
    assert policy.runtime_layers["effective"]["storage_workers"] == 1
    assert policy.service_lease_requirements["storage_guardian.archive"]["lane"] == "storage"
