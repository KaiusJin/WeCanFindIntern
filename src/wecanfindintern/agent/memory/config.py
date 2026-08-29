"""Tunable memory settings with environment overrides."""

from __future__ import annotations

import os


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


class MemorySettings:
    window_max_tokens: int = _int("AGENT_MEMORY_WINDOW_MAX_TOKENS", 8000)
    window_min_turns: int = _int("AGENT_MEMORY_WINDOW_MIN_TURNS", 2)
    window_max_turns: int = _int("AGENT_MEMORY_WINDOW_MAX_TURNS", 40)
    window_message_max_tokens: int = _int("AGENT_MEMORY_WINDOW_MESSAGE_MAX_TOKENS", 2000)
    window_fetch_limit: int = _int("AGENT_MEMORY_WINDOW_FETCH_LIMIT", 240)

    summary_trigger_tokens: int = _int("AGENT_MEMORY_SUMMARY_TRIGGER_TOKENS", 12000)
    summary_retain_tokens: int = _int("AGENT_MEMORY_SUMMARY_RETAIN_TOKENS", 5000)
    summary_max_tokens: int = _int("AGENT_MEMORY_SUMMARY_MAX_TOKENS", 2000)

    extraction_min_new_tokens: int = _int("AGENT_MEMORY_EXTRACTION_MIN_NEW_TOKENS", 200)
    extraction_max_messages: int = _int("AGENT_MEMORY_EXTRACTION_MAX_MESSAGES", 80)
    extraction_min_confidence: float = _float("AGENT_MEMORY_EXTRACTION_MIN_CONFIDENCE", 0.5)

    recall_limit: int = _int("AGENT_MEMORY_RECALL_LIMIT", 20)
    recall_max_tokens: int = _int("AGENT_MEMORY_RECALL_MAX_TOKENS", 3000)
    recall_fallback_limit: int = _int("AGENT_MEMORY_RECALL_FALLBACK_LIMIT", 10)
    recall_recency_half_life_days: float = _float(
        "AGENT_MEMORY_RECALL_RECENCY_HALF_LIFE_DAYS", 14.0
    )

    max_active_memories: int = _int("AGENT_MEMORY_MAX_ACTIVE_MEMORIES", 2000)
    maintenance_inline: bool = (
        os.getenv("AGENT_MEMORY_MAINTENANCE_INLINE", "0").strip().lower()
        in {"1", "true", "yes", "on"}
    )


settings = MemorySettings()
