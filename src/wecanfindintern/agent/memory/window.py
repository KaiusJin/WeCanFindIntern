"""Sliding context window with token budgets and per-message clipping."""

from __future__ import annotations

from uuid import UUID

from wecanfindintern.agent.memory.models import MemoryMessage, WindowSelection
from wecanfindintern.agent.memory.tokens import estimate_tokens

CLIP_MARKER = "\n[... truncated for context window ...]"


def select_window(
    messages: list[MemoryMessage],
    *,
    max_tokens: int,
    min_turns: int,
    max_turns: int,
    message_max_tokens: int,
) -> WindowSelection:
    """Select the most recent turns that fit the token budget.

    Newest-first so the turns closest to the current question always win.
    ``min_turns`` takes precedence over the budget; overlong messages are
    clipped to ``message_max_tokens`` instead of silently dropped.
    """

    ordered = sorted(messages, key=lambda message: (message.created_at, message.id))
    selected: list[MemoryMessage] = []
    clipped_ids: list[UUID] = []
    total = 0
    for message in reversed(ordered):
        if len(selected) >= max_turns:
            break
        candidate, candidate_tokens, was_clipped = clip_message(
            message, message_max_tokens
        )
        within_budget = total + candidate_tokens <= max_tokens
        if not within_budget and len(selected) >= min_turns:
            break
        selected.append(candidate)
        total += candidate_tokens
        if was_clipped:
            clipped_ids.append(message.id)
    selected.reverse()
    return WindowSelection(
        window=selected,
        token_count=total,
        clipped_message_ids=clipped_ids,
        excluded_message_count=len(ordered) - len(selected),
    )


def clip_message(
    message: MemoryMessage,
    message_max_tokens: int,
) -> tuple[MemoryMessage, int, bool]:
    """Clip an overlong message to the per-message token cap."""

    tokens = message.token_count or estimate_tokens(message.content)
    if tokens <= message_max_tokens:
        return message, tokens, False
    keep_chars = max(
        1, int(len(message.content) * (message_max_tokens / max(1, tokens)))
    )
    clipped_content = message.content[:keep_chars].rstrip() + CLIP_MARKER
    clipped = MemoryMessage(
        id=message.id,
        session_id=message.session_id,
        role=message.role,
        content=clipped_content,
        token_count=estimate_tokens(clipped_content),
        created_at=message.created_at,
    )
    return clipped, clipped.token_count, True


def should_compress(unsummarized_tokens: int, trigger_tokens: int) -> bool:
    """High-water mark check; compression only when the backlog is large."""

    return unsummarized_tokens > max(1, trigger_tokens)


def split_for_compression(
    messages: list[MemoryMessage],
    *,
    retain_tokens: int,
) -> tuple[list[MemoryMessage], list[MemoryMessage]]:
    """Split unsummarized history into (evict, retain).

    The newest ``retain_tokens`` worth of messages stay verbatim; everything
    older is folded into the rolling summary. Evicting to the low-water mark
    provides hysteresis so maintenance does not re-run next turn.
    """

    ordered = sorted(messages, key=lambda message: (message.created_at, message.id))
    retained: list[MemoryMessage] = []
    total = 0
    boundary = len(ordered)
    for index in range(len(ordered) - 1, -1, -1):
        message = ordered[index]
        tokens = message.token_count or estimate_tokens(message.content)
        if total + tokens > retain_tokens and retained:
            break
        retained.insert(0, message)
        total += tokens
        boundary = index
    evicted = ordered[:boundary]
    return evicted, retained
