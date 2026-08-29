"""Long-term memory recall: recency + confidence ranking, budget bounded."""

from __future__ import annotations

import math
from datetime import UTC, datetime

from wecanfindintern.agent.memory.config import settings
from wecanfindintern.agent.memory.models import MemoryRecord, RecalledMemory
from wecanfindintern.agent.memory.tokens import estimate_tokens


def _recency_factor(record: MemoryRecord, now: datetime) -> float:
    reference = record.updated_at or record.created_at
    if reference is None:
        return 0.5
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    age_days = max(0.0, (now - reference).total_seconds() / 86400.0)
    half_life = max(0.1, settings.recall_recency_half_life_days)
    return math.exp(-math.log(2.0) * age_days / half_life)


def _lexical_similarity(query: str, content: str) -> float:
    """Lightweight query-overlap signal when no embeddings are available."""

    query_tokens = {token for token in query.lower().split() if len(token) > 2}
    content_tokens = {token for token in content.lower().split() if len(token) > 2}
    if not query_tokens or not content_tokens:
        return 0.0
    overlap = len(query_tokens & content_tokens) / len(query_tokens)
    return min(1.0, overlap)


def rank_recalled_memories(
    records: list[MemoryRecord],
    *,
    query: str,
    now: datetime | None = None,
) -> list[RecalledMemory]:
    """Rank active memories by lexical match, recency and confidence."""

    now = now or datetime.now(UTC)
    scored: list[RecalledMemory] = []
    for record in records:
        similarity = _lexical_similarity(query, record.content)
        score = (
            0.6 * similarity
            + 0.2 * _recency_factor(record, now)
            + 0.2 * max(0.0, min(1.0, record.confidence))
        )
        scored.append(RecalledMemory(record=record, score=score))
    scored.sort(key=lambda item: item.score, reverse=True)
    return apply_budgets(scored)


def apply_budgets(ranked: list[RecalledMemory]) -> list[RecalledMemory]:
    selected: list[RecalledMemory] = []
    total_tokens = 0
    for item in ranked:
        if len(selected) >= settings.recall_limit:
            break
        tokens = estimate_tokens(item.record.content)
        if selected and total_tokens + tokens > settings.recall_max_tokens:
            continue
        selected.append(item)
        total_tokens += tokens
    return selected


def render_memories_for_prompt(recalled: list[RecalledMemory]) -> str:
    if not recalled:
        return ""
    lines = [
        "Known long-term context about this user (inferred, not authoritative):"
    ]
    lines.extend(
        f"- [{item.record.memory_type}] {item.record.content}"
        for item in recalled
    )
    return "\n".join(lines)


def recalled_token_count(recalled: list[RecalledMemory]) -> int:
    if not recalled:
        return 0
    return estimate_tokens(render_memories_for_prompt(recalled))
