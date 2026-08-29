"""Memory manager: context assembly, maintenance due, and maintenance runs."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from wecanfindintern.agent.memory.manager import AgentMemoryManager
from wecanfindintern.agent.memory.models import (
    ConversationSummary,
    MemoryCandidate,
    MemoryMessage,
    SessionMemoryState,
)
from wecanfindintern.agent.memory.tokens import estimate_tokens
from wecanfindintern.agent.tools import AgentDeps, LlmConfig


class FakeMemoryStore:
    def __init__(self):
        self.session_id = uuid4()
        self.state = SessionMemoryState(
            session_id=self.session_id,
            summary_text="Rolling summary so far.",
            summary_json='{"narrative":"Rolling summary so far."}',
            summary_version=1,
            summary_token_count=10,
            summary_covers_through_message_id=None,
            extraction_covers_through_message_id=None,
        )
        self.messages: list[MemoryMessage] = []
        self.preferences: dict[str, str] = {}
        self.memories: list[dict] = []
        self.saved_summaries: list[ConversationSummary] = []
        self.saved_memory_ids: list[UUID] = []
        self.extraction_watermark: UUID | None = None

    async def load_session_state(self, session_id):
        return self.state

    async def list_sessions_with_meta(self, *, limit=30):
        return [{"id": self.session_id, "title": "Test"}]

    async def touch_last_message(self, session_id):
        return None

    async def load_messages_after(self, session_id, after_message_id, limit):
        return self.messages[:limit]

    async def unsummarized_token_count(self, session_id, after_message_id):
        return sum(m.token_count for m in self.messages)

    async def save_summary(self, summary, expected_version):
        self.saved_summaries.append(summary)
        self.state = SessionMemoryState(
            session_id=self.state.session_id,
            summary_text=summary.summary_text,
            summary_json=summary.summary_json,
            summary_version=summary.version,
            summary_token_count=summary.token_count,
            summary_covers_through_message_id=summary.covers_through_message_id,
            extraction_covers_through_message_id=self.state.extraction_covers_through_message_id,
        )
        return True

    async def advance_extraction_watermark(self, session_id, through_message_id):
        self.extraction_watermark = through_message_id

    async def insert_memory(
        self, *, session_id, memory_type, content, confidence, source_message_id, expires_at
    ):
        content_hash = content.strip().lower()
        if any(m["content_hash"] == content_hash for m in self.memories):
            return None
        memory_id = uuid4()
        self.memories.append(
            {
                "id": memory_id,
                "content_hash": content_hash,
                "content": content,
                "memory_type": memory_type,
            }
        )
        return memory_id

    async def supersede_memory(self, old_memory_id, new_memory_id):
        return None

    async def load_active_memories(self, limit):
        return []

    async def touch_memory_access(self, memory_ids):
        return None

    async def count_active_memories(self):
        return len(self.memories)

    async def expire_lowest_value_memories(self, excess):
        return 0

    async def load_user_preferences(self):
        return dict(self.preferences)

    async def upsert_user_preference(self, key, value):
        self.preferences[key] = value

    async def delete_user_preference(self, key):
        return self.preferences.pop(key, None) is not None


def _message(content: str, *, index: int = 0) -> MemoryMessage:
    return MemoryMessage(
        id=uuid4(),
        session_id=UUID(int=1),
        role="user" if index % 2 == 0 else "assistant",
        content=content,
        token_count=estimate_tokens(content),
        created_at=datetime.now(UTC) + timedelta(seconds=index),
    )


def _deps() -> AgentDeps:
    return AgentDeps(
        job_repo=None,
        tracker_repo=None,
        profile_repo=None,
        waterlooworks=None,
        llm_config=LlmConfig(provider="DeepSeek", model_name="deepseek-chat", api_key="x"),
    )


def test_build_context_assembles_summary_window_preferences():
    store = FakeMemoryStore()
    store.preferences = {"TARGET_LOCATIONS": "Toronto", "LONG_TERM_MEMORY": "ENABLED"}
    store.messages = [_message(f"turn {i}", index=i) for i in range(5)]
    manager = AgentMemoryManager(store=store)

    context = asyncio.run(manager.build_context(store.session_id, "hello"))
    assert context.summary_text == "Rolling summary so far."
    assert len(context.window) == 5
    assert context.window[-1].content == "turn 4"
    assert context.preferences["TARGET_LOCATIONS"] == "Toronto"
    assert context.diagnostics["summaryVersion"] == 1
    assert context.total_token_count > 0


def test_record_turn_and_maintenance_due():
    store = FakeMemoryStore()
    manager = AgentMemoryManager(store=store)
    assert asyncio.run(manager.record_turn(store.session_id)) is False

    store.messages = [_message("word " * 400, index=i) for i in range(12)]
    assert asyncio.run(manager.maintenance_due(store.session_id)) is True


def test_run_maintenance_compresses_and_extracts(monkeypatch):
    import wecanfindintern.agent.memory.manager as manager_module

    store = FakeMemoryStore()
    store.messages = [_message("word " * 300, index=i) for i in range(12)]
    manager = AgentMemoryManager(store=store)

    def fake_summary(deps, previous, evicted):
        return {
            "topicsCovered": ["job search"],
            "userGoals": ["find work"],
            "establishedFacts": [],
            "preferencesStated": [],
            "unresolvedQuestions": [],
            "importantMessageIds": [str(m.id) for m in evicted[:1]],
            "narrative": "Summarized conversation.",
        }

    monkeypatch.setattr(manager_module, "build_rolling_summary", fake_summary)
    monkeypatch.setattr(
        manager_module,
        "extract_memory_candidates",
        lambda deps, messages, summary: [
            MemoryCandidate(
                memory_type="USER_PREFERENCE",
                content="Prefers jobs in Toronto.",
                confidence=0.9,
                source_message_id=messages[0].id,
                ttl_days=None,
            )
        ],
    )

    report = asyncio.run(manager.run_maintenance(store.session_id, _deps()))
    assert report.summarized is True
    assert report.summary_version == 2
    assert report.evicted_message_count > 0
    assert report.extraction_ran is True
    assert report.memories_added == 1
    assert store.saved_summaries
    assert store.extraction_watermark is not None

    # Re-running extraction must deduplicate identical memory content.
    report2 = asyncio.run(manager.run_maintenance(store.session_id, _deps()))
    assert report2.memories_skipped >= 1


def test_run_maintenance_skips_extraction_when_disabled(monkeypatch):
    import wecanfindintern.agent.memory.manager as manager_module

    store = FakeMemoryStore()
    store.preferences = {"LONG_TERM_MEMORY": "DISABLED"}
    store.messages = [_message("word " * 300, index=i) for i in range(12)]
    manager = AgentMemoryManager(store=store)

    monkeypatch_calls = []

    def fake_extract(deps, messages, summary):
        monkeypatch_calls.append(1)
        return []

    monkeypatch.setattr(manager_module, "extract_memory_candidates", fake_extract)
    report = asyncio.run(manager.run_maintenance(store.session_id, _deps()))
    assert report.extraction_ran is False
    assert monkeypatch_calls == []


def test_set_and_clear_preference():
    store = FakeMemoryStore()
    manager = AgentMemoryManager(store=store)
    value = asyncio.run(manager.set_preference("TARGET_LOCATIONS", " Toronto, Vancouver "))
    assert value == "Toronto, Vancouver"
    assert store.preferences["TARGET_LOCATIONS"] == "Toronto, Vancouver"
    assert asyncio.run(manager.clear_preference("TARGET_LOCATIONS")) is True

    with pytest.raises(ValueError):
        asyncio.run(manager.set_preference("NOT_A_KEY", "x"))
