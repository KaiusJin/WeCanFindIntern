"""Sliding window and compression splitting tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from wecanfindintern.agent.memory.models import MemoryMessage
from wecanfindintern.agent.memory.tokens import estimate_tokens
from wecanfindintern.agent.memory.window import (
    clip_message,
    select_window,
    should_compress,
    split_for_compression,
)


def _message(content: str, *, index: int = 0) -> MemoryMessage:
    return MemoryMessage(
        id=uuid4(),
        session_id=UUID(int=1),
        role="user" if index % 2 == 0 else "assistant",
        content=content,
        token_count=estimate_tokens(content),
        created_at=datetime(2026, 8, 28, 12, index, tzinfo=UTC),
    )


def test_select_window_respects_token_budget_and_min_turns():
    messages = [_message("short message", index=i) for i in range(8)]
    selection = select_window(
        messages,
        max_tokens=40,
        min_turns=2,
        max_turns=12,
        message_max_tokens=900,
    )
    assert 2 <= len(selection.window) <= 8
    assert selection.token_count <= 40 or len(selection.window) <= 2
    assert selection.excluded_message_count == len(messages) - len(selection.window)
    # Newest message is always included.
    assert selection.window[-1].id == messages[-1].id


def test_select_window_prefers_newest():
    messages = [_message(f"turn {i}", index=i) for i in range(6)]
    selection = select_window(
        messages,
        max_tokens=1000,
        min_turns=2,
        max_turns=3,
        message_max_tokens=900,
    )
    assert len(selection.window) == 3
    assert selection.window[-1].id == messages[-1].id
    assert selection.window[0].id == messages[-3].id


def test_clip_message_caps_overlong_message():
    message = _message("word " * 2000)
    clipped, tokens, was_clipped = clip_message(message, message_max_tokens=50)
    assert was_clipped is True
    assert tokens <= 80
    assert clipped.token_count < message.token_count
    assert clipped.content.endswith("[... truncated for context window ...]")


def test_should_compress_high_water_mark():
    assert should_compress(0, 3200) is False
    assert should_compress(3201, 3200) is True


def test_split_for_compression_keeps_newest_low_water_mark():
    messages = [_message(f"message {i} with some body text", index=i) for i in range(20)]
    evicted, retained = split_for_compression(messages, retain_tokens=60)
    assert evicted
    assert retained
    assert retained[-1].id == messages[-1].id
    assert sum(m.token_count for m in retained) <= 60
    assert all(m.id not in {r.id for r in retained} for m in evicted)
