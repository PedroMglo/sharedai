"""Shared LLM settings primitives for independent services."""

from __future__ import annotations

import os
from typing import Any

from pydantic_settings import BaseSettings


class CommonLLMSettings(BaseSettings):
    base_url: str = "https://localhost:11434"
    model: str = "llama3.1:8b"
    temperature: float = 0.2
    max_tokens: int = 512
    timeout_seconds: float = 15.0


def llm_settings_data(toml_llm: dict[str, Any], *, env_prefix: str) -> dict[str, Any]:
    data = {k: v for k, v in toml_llm.items() if v is not None}
    service_base_url = os.getenv(f"{env_prefix}_LLM_BASE_URL")
    if service_base_url:
        data["base_url"] = service_base_url
    elif os.getenv("OLLAMA_BASE_URL"):
        data["base_url"] = os.getenv("OLLAMA_BASE_URL")
    service_model = os.getenv(f"{env_prefix}_LLM_MODEL")
    if service_model:
        data["model"] = service_model
    return data
