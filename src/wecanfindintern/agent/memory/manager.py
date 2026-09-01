"""Facade: assemble turn context (read path) and run maintenance (write path)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from wecanfindintern.agent.contracts import AgentDeps
from wecanfindintern.agent.memory.config import settings
from wecanfindintern.agent.memory.extraction import extract_memory_candidates
from wecanfindintern.agent.memory.models import (
    ConversationSummary,
    MaintenanceReport,
    MemoryRecord,
    WorkingContext,
)
from wecanfindintern.agent.memory.preferences import (
    long_term_memory_enabled,
    render_preferences_for_prompt,
)
from wecanfindintern.agent.memory.recall import (
    rank_recalled_memories,
    recalled_token_count,
    render_memories_for_prompt,
)
from wecanfindintern.agent.memory.store import AgentMemoryStore
from wecanfindintern.agent.memory.summarizer import (
    build_rolling_summary,
    summary_text,
    summary_token_count,
)
from wecanfindintern.agent.memory.window import (
    select_window,
    should_compress,
    split_for_compression,
)

logger = logging.getLogger(__name__)


class AgentMemoryManager:
    """Read/write facade for short-term and long-term agent memory.

    The hot read path (``build_context``) performs bounded queries and no LLM
    calls. All LLM work (summary compression, memory extraction) happens in
    ``run_maintenance``, which is designed to run as a background task.
    """

    def __init__(
        self,
        store: AgentMemoryStore | None = None,
    ) -> None:
        self._store = store
        self._maintenance_tasks: dict[UUID, asyncio.Task[None]] = {}

    def store(self) -> AgentMemoryStore:
        if self._store is None:
            raise RuntimeError("AgentMemoryManager has no store configured.")
        return self._store

    async def get_preferences(self) -> dict[str, str]:
        return await self.store().load_user_preferences()

    async def set_preference(self, key: str, value: str) -> str:
        from wecanfindintern.agent.memory.preferences import validate_preference

        normalized = validate_preference(key, value)
        await self.store().upsert_user_preference(key, normalized)
        return normalized

    async def clear_preference(self, key: str) -> bool:
        return await self.store().delete_user_preference(key)

    async def build_context(
        self,
        session_id: UUID,
        current_query: str,
    ) -> WorkingContext:
        store = self.store()
        state = await store.load_session_state(session_id)
        preferences = await store.load_user_preferences()
        messages = await store.load_messages_after(
            session_id,
            state.summary_covers_through_message_id,
            settings.window_fetch_limit,
        )
        selection = select_window(
            messages,
            max_tokens=settings.window_max_tokens,
            min_turns=settings.window_min_turns,
            max_turns=settings.window_max_turns,
            message_max_tokens=settings.window_message_max_tokens,
        )
        recalled = []
        recall_diagnostics: dict = {"recallMode": "disabled_by_preference"}
        if long_term_memory_enabled(preferences):
            records = await store.load_active_memories(settings.recall_fallback_limit * 4)
            recalled = rank_recalled_memories(records, query=current_query)
            recall_diagnostics = {
                "recallMode": "lexical_recency_confidence",
                "recalledCount": len(recalled),
                "activeMemoryCount": len(records),
            }
            if recalled:
                await store.touch_memory_access(
                    [item.record.id for item in recalled]
                )
        return WorkingContext(
            session_id=session_id,
            summary_text=state.summary_text,
            window=selection.window,
            recalled_memories=recalled,
            preferences=preferences,
            window_token_count=selection.token_count,
            summary_token_count=state.summary_token_count,
            memory_token_count=recalled_token_count(recalled),
            diagnostics={
                "summaryVersion": state.summary_version,
                "windowClippedMessageIds": [
                    str(item) for item in selection.clipped_message_ids
                ],
                "windowExcludedMessageCount": selection.excluded_message_count,
                **recall_diagnostics,
            },
        )

    async def record_turn(self, session_id: UUID) -> bool:
        """Mark the turn recorded; returns True when maintenance is due."""

        await self.store().touch_last_message(session_id)
        return await self.maintenance_due(session_id)

    async def maintenance_due(self, session_id: UUID) -> bool:
        store = self.store()
        state = await store.load_session_state(session_id)
        summary_backlog = await store.unsummarized_token_count(
            session_id, state.summary_covers_through_message_id
        )
        if should_compress(summary_backlog, settings.summary_trigger_tokens):
            return True
        extraction_backlog = await store.unsummarized_token_count(
            session_id, state.extraction_covers_through_message_id
        )
        return extraction_backlog >= settings.extraction_min_new_tokens

    def schedule_maintenance(self, session_id: UUID, deps: AgentDeps) -> None:
        """Kick off background maintenance (idempotent per session)."""

        running = self._maintenance_tasks.get(session_id)
        if running and not running.done():
            return
        task = asyncio.create_task(self.run_maintenance(session_id, deps))
        self._maintenance_tasks[session_id] = task
        # Drop the entry once the task settles, otherwise the dict grows by
        # one dead entry per session for the lifetime of the process.
        task.add_done_callback(
            lambda _task: self._maintenance_tasks.pop(session_id, None)
        )

    async def shutdown(self) -> None:
        """Cancel and join process-scoped maintenance tasks before pool shutdown."""

        tasks = list(self._maintenance_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._maintenance_tasks.clear()

    async def run_maintenance(
        self, session_id: UUID, deps: AgentDeps
    ) -> MaintenanceReport:
        state = await self.store().load_session_state(session_id)
        errors: list[str] = []

        summarized = False
        summary_version: int | None = None
        evicted_count = 0
        try:
            summarized, summary_version, evicted_count = (
                await self._compress_if_needed(state, deps)
            )
        except Exception as error:
            errors.append(f"summary: {str(error)[:500]}")
            logger.warning("Agent summary maintenance failed for %s: %s", session_id, error)

        extraction_ran = False
        candidates_extracted = added = updated = skipped = 0
        try:
            (
                extraction_ran,
                candidates_extracted,
                added,
                updated,
                skipped,
            ) = await self._extract_and_consolidate(state, deps)
        except Exception as error:
            errors.append(f"extraction: {str(error)[:500]}")
            logger.warning(
                "Agent extraction maintenance failed for %s: %s", session_id, error
            )

        return MaintenanceReport(
            session_id=session_id,
            summarized=summarized,
            summary_version=summary_version,
            evicted_message_count=evicted_count,
            extraction_ran=extraction_ran,
            candidates_extracted=candidates_extracted,
            memories_added=added,
            memories_updated=updated,
            memories_skipped=skipped,
            errors=errors,
        )

    async def _compress_if_needed(
        self, state, deps: AgentDeps
    ) -> tuple[bool, int | None, int]:
        store = self.store()
        backlog_tokens = await store.unsummarized_token_count(
            state.session_id, state.summary_covers_through_message_id
        )
        if not should_compress(backlog_tokens, settings.summary_trigger_tokens):
            return False, None, 0
        messages = await store.load_messages_after(
            state.session_id,
            state.summary_covers_through_message_id,
            200,
        )
        evicted, _retained = split_for_compression(
            messages, retain_tokens=settings.summary_retain_tokens
        )
        if not evicted:
            return False, None, 0
        summary = await asyncio.to_thread(
            build_rolling_summary,
            deps,
            state.summary_json,
            evicted,
        )
        text = summary_text(summary)
        last = evicted[-1]
        saved = await store.save_summary(
            ConversationSummary(
                session_id=state.session_id,
                version=state.summary_version + 1,
                summary_text=text,
                summary_json=json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
                token_count=summary_token_count(text),
                covered_message_count=len(evicted),
                covers_through_message_id=last.id,
                provider=deps.llm_config.provider if deps.llm_config else "",
                model=deps.llm_config.model_name if deps.llm_config else "",
            ),
            expected_version=state.summary_version,
        )
        if not saved:
            return False, None, 0
        return True, state.summary_version + 1, len(evicted)

    async def _extract_and_consolidate(
        self, state, deps: AgentDeps
    ) -> tuple[bool, int, int, int, int]:
        store = self.store()
        preferences = await store.load_user_preferences()
        if not long_term_memory_enabled(preferences):
            return False, 0, 0, 0, 0
        messages = await store.load_messages_after(
            state.session_id,
            state.extraction_covers_through_message_id,
            settings.extraction_max_messages,
        )
        conversational = [
            message
            for message in messages
            if message.role in {"user", "assistant"}
        ]
        new_tokens = sum(message.token_count for message in conversational)
        if not conversational or new_tokens < settings.extraction_min_new_tokens:
            return False, 0, 0, 0, 0
        candidates = await asyncio.to_thread(
            extract_memory_candidates,
            deps,
            conversational,
            state.summary_text,
        )
        existing = await store.load_active_memories(settings.max_active_memories)
        added = updated = skipped = 0
        for candidate in candidates:
            new_id = await store.insert_memory(
                session_id=state.session_id,
                memory_type=candidate.memory_type,
                content=candidate.content,
                confidence=candidate.confidence,
                source_message_id=candidate.source_message_id,
                expires_at=(
                    datetime.now(UTC) + timedelta(days=candidate.ttl_days)
                    if candidate.ttl_days
                    else None
                ),
            )
            if new_id is None:
                skipped += 1
            else:
                match = _find_similar_memory(existing, candidate)
                if match is not None:
                    await store.supersede_memory(match.id, new_id)
                    updated += 1
                    existing = [item for item in existing if item.id != match.id]
                else:
                    added += 1
                existing.append(
                    MemoryRecord(
                        id=new_id,
                        session_id=state.session_id,
                        memory_type=candidate.memory_type,
                        content=candidate.content,
                        content_hash="",
                        confidence=candidate.confidence,
                    )
                )
        last = messages[-1]
        await store.advance_extraction_watermark(state.session_id, last.id)
        active_count = await store.count_active_memories()
        excess = active_count - settings.max_active_memories
        if excess > 0:
            await store.expire_lowest_value_memories(excess)
        return True, len(candidates), added, updated, skipped


_MEMORY_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "with",
    "without",
    "in",
    "on",
    "at",
    "to",
    "for",
    "of",
    "is",
    "are",
    "be",
    "it",
    "this",
    "that",
}

_MEMORY_TYPE_GROUPS: dict[str, set[str]] = {
    "EDUCATION_PROFILE": {"EDUCATION_PROFILE", "CAREER_CONTEXT"},
    "CAREER_CONTEXT": {"EDUCATION_PROFILE", "CAREER_CONTEXT"},
}


def _memory_tokens(content: str) -> set[str]:
    text = content.lower()
    tokens = set(re.findall(r"[a-z0-9]+", text)) - _MEMORY_STOPWORDS
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    tokens.update(cjk[index] + cjk[index + 1] for index in range(len(cjk) - 1))
    return tokens


def _memory_similarity(left: str, right: str) -> float:
    left_tokens = _memory_tokens(left)
    right_tokens = _memory_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))


def _find_similar_memory(existing: list[MemoryRecord], candidate) -> MemoryRecord | None:
    """Find an active memory the new candidate should supersede (same topic)."""

    for record in existing:
        compatible = _MEMORY_TYPE_GROUPS.get(record.memory_type, {record.memory_type})
        if candidate.memory_type not in compatible:
            continue
        if _memory_similarity(record.content, candidate.content) >= 0.5:
            return record
    return None


def render_context_sections(context: WorkingContext) -> list[str]:
    """Prompt-facing rendering of the assembled working context."""

    sections: list[str] = []
    preferences_block = render_preferences_for_prompt(context.preferences)
    if preferences_block:
        sections.append(preferences_block)
    if context.summary_text:
        sections.append(
            "## Conversation summary (state, not evidence)\n" + context.summary_text
        )
    memories_block = render_memories_for_prompt(context.recalled_memories)
    if memories_block:
        sections.append(memories_block)
    if context.window:
        lines = [
            f"{message.role}: {message.content[:2000]}"
            for message in context.window
        ]
        sections.append("## Recent conversation\n" + "\n".join(lines))
    return sections
