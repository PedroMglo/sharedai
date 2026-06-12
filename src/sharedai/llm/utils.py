"""Shared LLM utilities used across local LLM backends."""

from __future__ import annotations

import re
from urllib.parse import urlparse

_THINK_PATTERN = re.compile(r"<think\b[^>]*>.*?</think>", re.DOTALL | re.IGNORECASE)
_OPEN_THINK_PATTERN = re.compile(r"<think\b[^>]*>.*", re.DOTALL | re.IGNORECASE)
_THINK_TAG_PATTERN = re.compile(r"</?think\b[^>]*>", re.IGNORECASE)


def strip_think(text: str) -> str:
    """Remove <think>...</think> blocks from LLM output."""
    cleaned = _THINK_PATTERN.sub("", text or "")
    cleaned = _OPEN_THINK_PATTERN.sub("", cleaned)
    cleaned = _THINK_TAG_PATTERN.sub("", cleaned)
    return cleaned.strip()


def mask_url(url: str) -> str:
    """Return URL with credentials masked: https://user:***@host/path."""
    try:
        parsed = urlparse(url)
        if parsed.password:
            netloc = f"{parsed.hostname}:{parsed.port}" if parsed.port else (parsed.hostname or "")
            return parsed._replace(netloc=netloc).geturl()
    except Exception:
        pass
    return url
