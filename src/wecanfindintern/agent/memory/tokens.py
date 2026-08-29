"""Cheap token estimation for prompt budgeting (no tokenizer dependency)."""

from __future__ import annotations

import math


def estimate_tokens(text: str | None) -> int:
    """Rough token estimate: ~4 characters per token, at least 1."""

    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))
