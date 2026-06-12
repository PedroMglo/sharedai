"""Shared HTTP calls for local LLM chat completions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from sharedai.llm.backend_url import llm_chat_url
from sharedai.llm.chat_payload import build_chat_payload, parse_chat_content

PostCallable = Callable[..., httpx.Response]


def call_chat_completion(
    *,
    model: str,
    messages: list[dict[str, str]],
    base_url: str,
    temperature: float,
    max_tokens: int,
    timeout: float,
    post: PostCallable = httpx.post,
) -> str:
    payload: dict[str, Any] = build_chat_payload(
        model=model,
        messages=messages,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    response = post(llm_chat_url(base_url), json=payload, timeout=timeout)
    response.raise_for_status()
    return parse_chat_content(response.json())
