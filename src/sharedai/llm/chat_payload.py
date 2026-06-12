"""Shared helpers for local chat-completion payloads."""

from __future__ import annotations

from typing import Any

from sharedai.llm.backend_url import disables_thinking, is_ollama_backend
from sharedai.llm.utils import strip_think


def build_chat_payload(
    *,
    model: str,
    messages: list[dict[str, str]],
    base_url: str,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    if is_ollama_backend(base_url):
        payload["options"] = {
            "temperature": temperature,
            "num_predict": max_tokens,
        }
        payload["think"] = False
    else:
        payload["temperature"] = temperature
        payload["max_tokens"] = max_tokens
    if disables_thinking(base_url):
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    return payload


def parse_chat_content(data: dict[str, Any]) -> str:
    if "message" in data:
        return strip_think(str(data["message"]["content"]))
    if "choices" in data:
        return strip_think(str(data["choices"][0]["message"]["content"]))
    raise ValueError("Unexpected LLM response format")
