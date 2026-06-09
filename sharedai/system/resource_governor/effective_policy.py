"""Machine-adaptive effective policy derivation."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from sharedai.system.resource_governor.schemas import EffectivePolicy, GovernorMode

ROOT = Path(__file__).resolve().parents[3]


def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    import os

    for raw in (
        os.environ.get("AI_LOCAL_ROOT"),
        os.environ.get("ORC_LIFECYCLE_PROJECT_DIR"),
        "/project",
        "/home/pmglo/_projects/ai-local",
        str(Path.cwd()),
        str(ROOT),
    ):
        if not raw:
            continue
        path = Path(raw)
        if path not in roots:
            roots.append(path)
    return roots


def _default_policy_path() -> Path:
    for root in _candidate_roots():
        candidate = root / "config" / "resource_governor.yaml"
        if candidate.exists():
            return candidate
    return ROOT / "config" / "resource_governor.yaml"


DEFAULT_POLICY_PATH = _default_policy_path()


def _load_policy(path: Path = DEFAULT_POLICY_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _decision_value(resolved: dict[str, Any], field: str, default: Any = None) -> Any:
    for decision in resolved.get("decisions", []):
        if decision.get("field") == field:
            return decision.get("value", default)
    return default


def _runtime_dict(resolved: dict[str, Any] | None) -> dict[str, Any]:
    if not resolved:
        return {}
    runtime = resolved.get("runtime", {})
    return runtime if isinstance(runtime, dict) else {}


def _derive_machine_profile(runtime: dict[str, Any], configured_profile: str = "auto") -> str:
    if configured_profile and configured_profile != "auto":
        return configured_profile

    cpu_threads = int(runtime.get("cpu_threads") or 1)
    ram_total = runtime.get("ram_total_gb")
    ram_gb = float(ram_total) if ram_total is not None else 0.0
    gpu_available = bool(runtime.get("gpu_available"))
    vram_total = runtime.get("vram_total_gb")
    vram_gb = float(vram_total) if vram_total is not None else 0.0

    if not gpu_available or vram_gb <= 0:
        if ram_gb and ram_gb <= 8:
            return "tiny_cpu_only"
        if ram_gb and ram_gb <= 16:
            return "low_ram_cpu"
        return "balanced_desktop" if cpu_threads >= 8 else "low_ram_cpu"
    if vram_gb <= 4:
        return "gpu_4gb"
    if vram_gb <= 6:
        return "gpu_6gb"
    if vram_gb <= 8:
        return "gpu_8gb"
    if vram_gb >= 16 and ram_gb >= 48 and cpu_threads >= 16:
        return "workstation"
    return "balanced_desktop"


def _profile_limits(profile: str, runtime: dict[str, Any]) -> dict[str, Any]:
    cpu_threads = int(runtime.get("cpu_threads") or 1)
    physical_cores = max(1, math.ceil(cpu_threads / 2))
    ram_gb = float(runtime.get("ram_total_gb") or 8)
    vram_gb = float(runtime.get("vram_total_gb") or 0)
    reserved_cpu = max(1, math.ceil(physical_cores * 0.15))
    available_cpu = max(1, physical_cores - reserved_cpu)
    reserved_ram_gb = max(2.0, ram_gb * 0.18)
    reserved_vram_gb = max(0.5, vram_gb * 0.12) if vram_gb > 0 else 0.0

    weak = profile in {"tiny_cpu_only", "low_ram_cpu", "low_vram_gpu", "gpu_4gb", "gpu_6gb"}
    gpu8 = profile == "gpu_8gb"
    max_workers = 1 if weak else (2 if gpu8 else max(2, min(8, available_cpu)))
    max_embedding_batch = 2 if profile == "tiny_cpu_only" else (4 if weak else (8 if gpu8 else 32))
    heavy_gpu_concurrency = 0 if vram_gb <= 0 else (1 if profile != "workstation" else max(1, min(2, int(vram_gb // 12))))

    return {
        "physical_cores": physical_cores,
        "reserved_cpu_cores": reserved_cpu,
        "available_cpu_cores": available_cpu,
        "reserved_ram_gb": round(reserved_ram_gb, 2),
        "reserved_vram_gb": round(reserved_vram_gb, 2),
        "max_workers": max_workers,
        "background_workers": 1 if weak else max(1, math.floor(available_cpu * 0.35)),
        "storage_workers": 1,
        "embedding_batch": max_embedding_batch,
        "heavy_gpu_concurrency": heavy_gpu_concurrency,
        "graph_deferred": weak,
        "reranker": "only_if_uncertain" if weak or gpu8 else "adaptive",
        "audio_gpu": "idle_only" if weak or gpu8 else "lease_required",
        "model_keep_alive_auxiliary_seconds": 30 if weak else 120,
    }


def _default_conflict_matrix(policy_root: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    configured = policy_root.get("gpu_conflict_matrix")
    if isinstance(configured, dict) and configured:
        return configured
    return {
        "chat_stream": {
            "blocks": [
                "embedding_gpu_batch",
                "audio_transcribe_gpu",
                "graph_llm",
                "deep_reasoning_batch",
                "model_warmup",
            ],
        },
        "embedding_gpu_batch": {"blocks": ["chat_stream", "audio_transcribe_gpu", "graph_llm"]},
        "audio_transcribe_gpu": {"blocks": ["chat_stream", "embedding_gpu_batch", "graph_llm"]},
        "graph_llm": {"blocks": ["chat_stream", "embedding_gpu_batch", "audio_transcribe_gpu"]},
        "model_warmup": {"blocks": ["chat_stream"]},
    }


def _merge_nested(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            nested = dict(result[key])
            nested.update(value)
            result[key] = nested
        else:
            result[key] = value
    return result


def _default_service_lease_requirements() -> dict[str, Any]:
    return {
        "orchestrator.chat": {
            "lane": "interactive",
            "capability": "chat_stream",
            "resource_class": "model_runtime",
            "required": True,
        },
        "rag.embedding": {
            "lane": "background",
            "capability": "embedding_gpu_batch",
            "resource_class": "vram",
            "required": True,
            "fallback_capability": "embedding_cpu_batch",
        },
        "rag.graphify": {
            "lane": "background",
            "capability": "graph_llm",
            "resource_class": "model_runtime",
            "required": True,
        },
        "storage_guardian.archive": {
            "lane": "storage",
            "capability": "storage_archive",
            "resource_class": "io_write",
            "required": True,
        },
        "audio_transcribe.gpu": {
            "lane": "heavy_gpu",
            "capability": "audio_transcribe_gpu",
            "resource_class": "vram",
            "required": True,
            "fallback_capability": "audio_transcribe_cpu",
        },
        "model.warmup": {
            "lane": "heavy_gpu",
            "capability": "model_warmup",
            "resource_class": "model_runtime",
            "required": True,
        },
    }


def _runtime_layers(runtime: dict[str, Any], limits: dict[str, Any]) -> dict[str, Any]:
    host = {
        "cpu_threads": runtime.get("cpu_threads"),
        "ram_total_gb": runtime.get("ram_total_gb"),
        "ram_available_gb": runtime.get("ram_available_gb"),
        "gpu_available": runtime.get("gpu_available"),
        "gpu_name": runtime.get("gpu_name"),
        "vram_total_gb": runtime.get("vram_total_gb"),
    }
    docker = {
        "available": runtime.get("docker_available"),
        "context": runtime.get("docker_context"),
        "memory_gb": runtime.get("docker_memory_gb") or runtime.get("daemon_memory_gb"),
        "cpus": runtime.get("docker_cpus") or runtime.get("daemon_cpus"),
        "gpu_available": runtime.get("docker_gpu_available", runtime.get("gpu_available")),
    }
    container = {
        "in_container": runtime.get("in_container"),
        "memory_limit_gb": runtime.get("cgroup_memory_limit_gb") or runtime.get("container_memory_limit_gb"),
        "memory_current_gb": runtime.get("cgroup_memory_current_gb") or runtime.get("container_memory_current_gb"),
        "cpu_quota": runtime.get("cgroup_cpu_quota") or runtime.get("container_cpu_quota"),
        "cpuset": runtime.get("cgroup_cpuset") or runtime.get("container_cpuset"),
        "gpu_visible": runtime.get("container_gpu_visible"),
    }
    return {
        "host": host,
        "docker": docker,
        "container": container,
        "effective": {
            "cpu_workers": limits.get("max_workers"),
            "background_workers": limits.get("background_workers"),
            "storage_workers": limits.get("storage_workers"),
            "heavy_gpu_concurrency": limits.get("heavy_gpu_concurrency"),
            "embedding_batch": limits.get("embedding_batch"),
        },
    }


def _storage_policy(resolved: dict[str, Any] | None) -> dict[str, Any]:
    resolved = resolved or {}
    storage_paths = resolved.get("storage_paths") if isinstance(resolved.get("storage_paths"), dict) else {}
    config_storage = (
        ((resolved.get("config") or {}).get("storage") or {})
        if isinstance(resolved.get("config"), dict)
        else {}
    )
    runtime = _runtime_dict(resolved)
    mode = storage_paths.get("AI_LOCAL_STORAGE_MODE") or _decision_value(resolved, "storage.mode", "unknown")
    external_root = storage_paths.get("AI_STORAGE_EXTERNAL_ROOT") or config_storage.get("external_root")
    external_available = bool(runtime.get("storage_exists") and runtime.get("storage_mounted") and runtime.get("storage_writable"))
    fallback_enabled = bool(config_storage.get("allow_local_heavy_fallback", False))
    return {
        "effective_mode": mode,
        "external_root": str(external_root) if external_root else "",
        "external_configured": bool(external_root),
        "external_available": external_available,
        "require_external": bool(config_storage.get("require_external", False)),
        "local_fallback_enabled": fallback_enabled,
        "fallback_is_operational": mode == "local_fallback",
        "missing_external_is_blocker": mode == "external_missing",
        "note": "external storage absence is expected while local_fallback is active"
        if mode == "local_fallback"
        else "",
    }


def _try_resolve_config() -> dict[str, Any] | None:
    try:
        import sys

        for root in _candidate_roots():
            if (root / "config" / "resolver.py").exists() and str(root) not in sys.path:
                sys.path.insert(0, str(root))
        from config.resolver import resolve_config

        return resolve_config()
    except Exception:
        return None


def build_effective_policy(
    *,
    resolved_config: dict[str, Any] | None = None,
    policy_path: str | Path | None = None,
) -> EffectivePolicy:
    """Derive a Resource Governor policy from central config and runtime probes."""
    resolved = resolved_config if resolved_config is not None else _try_resolve_config()
    policy_doc = _load_policy(Path(policy_path) if policy_path else DEFAULT_POLICY_PATH)
    policy_root = policy_doc.get("resource_governor", {}) if isinstance(policy_doc, dict) else {}
    runtime = _runtime_dict(resolved)
    configured_profile = (
        ((resolved or {}).get("config", {}).get("hardware", {}) or {}).get("profile", "auto")
        if isinstance((resolved or {}).get("config"), dict)
        else "auto"
    )
    profile = _derive_machine_profile(runtime, str(configured_profile))
    limits = _profile_limits(profile, runtime)
    weak = profile in {"tiny_cpu_only", "low_ram_cpu", "low_vram_gpu", "gpu_4gb", "gpu_6gb"}

    mode = str(policy_root.get("mode") or "observe_only")
    if mode not in {m.value for m in GovernorMode}:
        mode = "observe_only"

    experience = policy_root.get("experience_policy", {}) if isinstance(policy_root.get("experience_policy"), dict) else {}

    thresholds = {
        "swap_used_mb_hard": 256 if weak else 512,
        "swap_growth_mb_hard": 128,
        "memory_pressure_some_10s_hard": 0.20 if weak else 0.35,
        "io_pressure_some_10s_hard": 0.25 if weak else 0.40,
        "disk_free_ratio_hard": 0.12,
        "ram_available_reserve_gb": limits["reserved_ram_gb"],
        "vram_reserve_gb": limits["reserved_vram_gb"],
    }
    thresholds.update(policy_root.get("thresholds", {}) if isinstance(policy_root.get("thresholds"), dict) else {})

    slo_budget = {
        "first_token_target_ms": 800,
        "first_token_soft_limit_ms": 1500,
        "query_context_budget_ms": 350 if weak else 500,
        "reranker_budget_ms": 150 if weak else 250,
        "graph_context_budget_ms": 150 if weak else 300,
        "router_llm_budget_ms": 150 if weak else 200,
        "enrichment_total_budget_ms": 350 if weak else 700,
    }
    slo_budget.update(policy_root.get("experience_slo", {}) if isinstance(policy_root.get("experience_slo"), dict) else {})

    resource_classes = {
        "interactive_api": {"priority": "protected", "workers": max(1, math.ceil(limits["available_cpu_cores"] * 0.25))},
        "vector_store": {"priority": "protected_io", "workers": 1},
        "background_compute": {"priority": "preemptible", "workers": limits["background_workers"]},
        "storage_low_priority": {"priority": "lowest", "workers": limits["storage_workers"]},
        "observability": {"priority": "adaptive", "workers": 1},
    }
    configured_resource_classes = policy_root.get("resource_classes")
    if isinstance(configured_resource_classes, dict):
        resource_classes = _merge_nested(resource_classes, configured_resource_classes)

    service_lease_requirements = _default_service_lease_requirements()
    configured_requirements = policy_root.get("service_lease_requirements")
    if isinstance(configured_requirements, dict):
        service_lease_requirements = _merge_nested(service_lease_requirements, configured_requirements)

    lanes = {
        "interactive": {"priority": 100, "preemptible": False},
        "interactive_enrichment": {
            "priority": 75,
            "preemptible": True,
            "budget_ms": slo_budget["enrichment_total_budget_ms"],
        },
        "background": {"priority": 35, "preemptible": True, "workers": limits["background_workers"]},
        "storage": {"priority": 10, "preemptible": True, "workers": limits["storage_workers"]},
        "heavy_gpu": {"priority": 70, "preemptible": True, "concurrency": limits["heavy_gpu_concurrency"]},
    }

    return EffectivePolicy(
        mode=mode,
        machine_profile=profile,
        thin_but_capable=weak,
        foreground_first=bool(experience.get("foreground_first", True)),
        preserve_quality=bool(experience.get("preserve_quality", True)),
        allow_deferred_quality=bool(experience.get("allow_deferred_quality", True)),
        allow_silent_quality_loss=bool(experience.get("allow_silent_quality_loss", False)),
        lanes=lanes,
        thresholds=thresholds,
        slo_budget=slo_budget,
        limits=limits,
        resource_classes=resource_classes,
        gpu_conflict_matrix=_default_conflict_matrix(policy_root),
        fallback_policy=policy_root.get("fallback_policy", {}),
        service_lease_requirements=service_lease_requirements,
        operational_authority={
            "foreground_first": bool(experience.get("foreground_first", True)),
            "active_interaction_locks": {
                "storage": "defer",
                "heavy_gpu": "defer",
                "model_warmup": "defer",
                "background_gpu": "defer",
            },
            "pressure_gates": {
                "swap": "defer_non_interactive",
                "psi_memory": "defer_background_storage_gpu",
                "psi_io": "defer_background_storage_gpu",
                "disk_free": "defer_writes",
                "battery": "defer_background_storage_gpu_when_unplugged_low",
                "thermal": "defer_background_storage_gpu",
            },
        },
        storage_policy=_storage_policy(resolved),
        runtime_layers=_runtime_layers(runtime, limits),
        runtime={
            "cpu_threads": runtime.get("cpu_threads"),
            "ram_total_gb": runtime.get("ram_total_gb"),
            "gpu_available": runtime.get("gpu_available"),
            "gpu_name": runtime.get("gpu_name"),
            "vram_total_gb": runtime.get("vram_total_gb"),
            "runtime_workers_final": _decision_value(resolved or {}, "runtime.workers.final"),
            "embedding_batch_from_intelligence": _decision_value(
                resolved or {},
                "runtime.batch_size.final",
                _decision_value(resolved or {}, "runtime.batch_size"),
            ),
            "storage_mode": _decision_value(resolved or {}, "storage.mode"),
            "battery_percent": runtime.get("battery_percent"),
            "battery_power_plugged": runtime.get("battery_power_plugged"),
            "thermal_throttle": runtime.get("thermal_throttle"),
            "lid_closed": runtime.get("lid_closed"),
        },
    )
