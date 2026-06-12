"""Small token estimation helpers for local budgeting."""

from __future__ import annotations

import math


def estimate_tokens(text: str | None, *, chars_per_token: float = 4.0) -> int:
    """Return a conservative approximate token count for text."""

    if not text:
        return 0
    normalized = " ".join(str(text).split())
    if not normalized:
        return 0
    return max(1, math.ceil(len(normalized) / max(chars_per_token, 1.0)))
