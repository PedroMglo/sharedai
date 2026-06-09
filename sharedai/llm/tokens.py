"""Token estimation shared across all ai-local projects.

Uses word-boundary regex heuristic calibrated for multilingual (PT+EN) text
and code. ~1.3 tokens per word for LLaMA/BERT-family tokenizers.
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"""\w+|[^\w\s]""", re.UNICODE)


def estimate_tokens(text: str) -> int:
    """Estimate token count using word-boundary heuristic.

    More accurate than naive len//4 for mixed PT-PT/EN text and code.
    Returns at least 1 for non-empty text.
    """
    if not text:
        return 0
    words = _TOKEN_RE.findall(text)
    return max(1, int(len(words) * 1.3))
