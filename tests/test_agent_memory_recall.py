"""Memory recall ranking tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from wecanfindintern.agent.memory.models import MemoryRecord
from wecanfindintern.agent.memory.recall import (
    apply_budgets,
    rank_recalled_memories,
    render_memories_for_prompt,
)


def _record(content: str, *, updated_days_ago: int = 0, confidence: float = 0.8) -> MemoryRecord:
    now = datetime.now(UTC)
    return MemoryRecord(
        id=uuid4(),
        memory_type="USER_PREFERENCE",
        content=content,
        content_hash=content,
        confidence=confidence,
        created_at=now - timedelta(days=30),
        updated_at=now - timedelta(days=updated_days_ago),
    )


def test_rank_recalled_memories_prefers_query_match():
    toronto = _record("Prefers jobs in Toronto.")
    remote = _record("Prefers remote work.", updated_days_ago=1)
    ranked = rank_recalled_memories([remote, toronto], query="Toronto")
    assert ranked[0].record.content == toronto.content


def test_rank_recalled_memories_prefers_recency():
    old = _record("Prefers jobs in Toronto.", updated_days_ago=60, confidence=1.0)
    new = _record("Prefers jobs in Vancouver.", updated_days_ago=1, confidence=0.6)
    ranked = rank_recalled_memories([old, new], query="unrelated query here")
    assert ranked[0].record.content == new.content


def test_apply_budgets_limits_count_and_tokens():
    records = [
        _record(
            f"memory {i} with a reasonably long sentence of content",
            updated_days_ago=i,
        )
        for i in range(20)
    ]
    ranked = rank_recalled_memories(records, query="memory")
    assert len(ranked) <= 6
    budgeted = apply_budgets(ranked)
    assert len(budgeted) <= 6


def test_render_memories_for_prompt():
    ranked = rank_recalled_memories([_record("Prefers Toronto.")], query="Toronto")
    text = render_memories_for_prompt(ranked)
    assert "Known long-term context" in text
    assert "USER_PREFERENCE" in text
    assert render_memories_for_prompt([]) == ""
