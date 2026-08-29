"""Tunable memory settings with environment overrides."""

from __future__ import annotations

import os


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


class MemorySettings:
    window_max_tokens: int = _int("AGENT_MEMORY_WINDOW_MAX_TOKENS", 2400)
    window_min_turns: int = _int("AGENT_MEMORY_WINDOW_MIN_TURNS", 2)
    window_max_turns: int = _int("AGENT_MEMORY_WINDOW_MAX_TURNS", 12)
    window_message_max_tokens: int = _int("AGENT_MEMORY_WINDOW_MESSAGE_MAX_TOKENS", 900)
    window_fetch_limit: int = _int("AGENT_MEMORY_WINDOW_FETCH_LIMIT", 96)

    summary_trigger_tokens: int = _int("AGENT_MEMORY_SUMMARY_TRIGGER_TOKENS", 3200)
    summary_retain_tokens: int = _int("AGENT_MEMORY_SUMMARY_RETAIN_TOKENS", 1400)
    summary_max_tokens: int = _int("AGENT_MEMORY_SUMMARY_MAX_TOKENS", 700)

    extraction_min_new_tokens: int = _int("AGENT_MEMORY_EXTRACTION_MIN_NEW_TOKENS", 120)
    extraction_max_messages: int = _int("AGENT_MEMORY_EXTRACTION_MAX_MESSAGES", 40)
    extraction_min_confidence: float = _float("AGENT_MEMORY_EXTRACTION_MIN_CONFIDENCE", 0.5)

    recall_limit: int = _int("AGENT_MEMORY_RECALL_LIMIT", 6)
    recall_max_tokens: int = _int("AGENT_MEMORY_RECALL_MAX_TOKENS", 600)
    recall_fallback_limit: int = _int("AGENT_MEMORY_RECALL_FALLBACK_LIMIT", 4)
    recall_recency_half_life_days: float = _float(
        "AGENT_MEMORY_RECALL_RECENCY_HALF_LIFE_DAYS", 14.0
    )

    max_active_memories: int = _int("AGENT_MEMORY_MAX_ACTIVE_MEMORIES", 400)
    maintenance_inline: bool = (
        os.getenv("AGENT_MEMORY_MAINTENANCE_INLINE", "0").strip().lower()
        in {"1", "true", "yes", "on"}
    )


settings = MemorySettings()
