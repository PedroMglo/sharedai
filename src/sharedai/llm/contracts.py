"""Shared contracts for LLM configuration injected into services."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LLMConfigOverride(BaseModel):
    """LLM config injected by the runtime from the central model registry."""

    model: str = ""
    backend_type: str = "ollama"
    backend_url: str = ""
    system_prompt: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    extra_prompts: dict[str, Any] = Field(default_factory=dict)


class LLMIntermediateInput(BaseModel):
    """Typed trace shape for an intermediate LLM call."""

    agent_name: str
    operation: str
    query: str = ""
    llm_config: LLMConfigOverride | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMIntermediateOutput(BaseModel):
    """Typed trace shape for an intermediate LLM result."""

    agent_name: str
    operation: str
    output: str = ""
    model_used: str = ""
    success: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
