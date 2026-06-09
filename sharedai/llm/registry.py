"""Model registry — loads models.json and provides typed access.

Single source of truth for all model definitions, aliases, system prompts,
and parameters across the Local AI workspace.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_REGISTRY_DEFAULT = Path(__file__).resolve().parents[2] / "config" / "models" / "orc.config.json"


class ModelRegistry:
    """Parsed model registry with lookup helpers."""

    def __init__(self, data: dict[str, Any], mtime: float) -> None:
        self._data = data
        self._mtime = mtime
        self._alias_map: dict[str, str] = {}
        self._alias_to_role: dict[str, dict[str, Any]] = {}
        self._model_to_role: dict[str, dict[str, Any]] = {}
        self._build_alias_maps()

    def _build_alias_maps(self) -> None:
        for section_key in ("orchestration", "rag"):
            section = self._data.get(section_key, {})
            roles = section.get("roles", {})
            for _role_name, role_cfg in roles.items():
                model = role_cfg.get("model", "")
                self._model_to_role[model] = role_cfg
                for alias in role_cfg.get("aliases", []):
                    self._alias_map[alias] = model
                    self._alias_to_role[alias] = role_cfg

    def resolve_alias(self, name: str) -> str:
        """Resolve a model alias to its full model name."""
        if name in self._alias_map:
            return self._alias_map[name]
        routing_keys = self._data.get("orchestration", {}).get("routing_keys", {})
        if name in routing_keys:
            role_name = routing_keys[name]
            roles = self._data.get("orchestration", {}).get("roles", {})
            role = roles.get(role_name)
            if role:
                return role["model"]
        return name

    def get_all_aliases(self) -> dict[str, str]:
        return dict(self._alias_map)

    def get_role(self, section: str, role_name: str) -> dict[str, Any] | None:
        return self._data.get(section, {}).get("roles", {}).get(role_name)

    def is_rag_capable(self, model_name: str) -> bool:
        """Check if a model has RAG capability enabled."""
        role = self._alias_to_role.get(model_name)
        if role is not None:
            return role.get("rag_capable", True)
        role = self._model_to_role.get(model_name)
        if role is not None:
            return role.get("rag_capable", True)
        return True

    def get_default_chat_model(self) -> str:
        orch = self._data.get("orchestration", {})
        role_name = orch.get("default_chat_role", "default-conversation")
        role = orch.get("roles", {}).get(role_name)
        if role:
            return role["model"]
        return ""

    def get_prompt(self, key: str) -> str:
        return self._data.get("rag", {}).get("prompts", {}).get(key, "")

    def get_system_prompt(self, section: str, role_name: str) -> str:
        role = self.get_role(section, role_name)
        return role.get("system_prompt", "") if role else ""

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    @property
    def orchestration(self) -> dict[str, Any]:
        return self._data.get("orchestration", {})

    @property
    def rag(self) -> dict[str, Any]:
        return self._data.get("rag", {})

    def get_model_for_key(self, key: str) -> str:
        """Get the model name for a given key.

        Lookup order:
          1. orchestration.routing.profiles.{key}.model
          2. rag.roles.{key}.model
          3. Empty string (caller should provide its own default)
        """
        profile = self._data.get("orchestration", {}).get("routing", {}).get("profiles", {}).get(key)
        if profile:
            return profile.get("model", "")
        rag_role = self._data.get("rag", {}).get("roles", {}).get(key)
        if rag_role:
            return rag_role.get("model", "")
        return ""


# ---------------------------------------------------------------------------
# Singleton with hot-reload
# ---------------------------------------------------------------------------

_registry: ModelRegistry | None = None
_registry_path: Path | None = None


def _find_registry_path() -> Path:
    env = os.environ.get("AI_MODELS_REGISTRY")
    if env:
        return Path(env).expanduser()
    return _REGISTRY_DEFAULT


def get_registry() -> ModelRegistry:
    """Get the model registry singleton, auto-reloading if the file changed."""
    global _registry, _registry_path

    if _registry_path is None:
        _registry_path = _find_registry_path()

    if not _registry_path.exists():
        raise FileNotFoundError(
            f"Model registry not found at {_registry_path}. "
            "Set AI_MODELS_REGISTRY or place the registry under config/models/orc.config.json."
        )

    current_mtime = _registry_path.stat().st_mtime
    if _registry is not None and current_mtime == _registry._mtime:
        return _registry

    with open(_registry_path) as f:
        data = json.load(f)

    _registry = ModelRegistry(data, current_mtime)
    log.info("sharedai.llm: registry loaded from %s", _registry_path)
    return _registry


def _reset_registry() -> None:
    """Reset singleton — for testing."""
    global _registry, _registry_path
    _registry = None
    _registry_path = None
