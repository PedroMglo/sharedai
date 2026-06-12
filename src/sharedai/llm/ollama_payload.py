"""Shared helpers for Ollama native generate payloads."""

from __future__ import annotations

from typing import Any

from sharedai.llm.utils import strip_think


def build_generate_payload(
    *,
    model: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    num_ctx: int | None = None,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "temperature": temperature,
        "num_predict": max_tokens,
    }
    if num_ctx is not None:
        options["num_ctx"] = num_ctx
    return {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": options,
    }


def parse_generate_content(data: dict[str, Any]) -> str:
    return strip_think(str(data.get("response", "")))
