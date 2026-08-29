"""Orchestrator flow tests with fake persistence and stubbed LLM planning."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

import wecanfindintern.agent.orchestrator as orchestrator_module
from wecanfindintern.agent.memory.models import WorkingContext
from wecanfindintern.agent.models import (
    AgentApproval,
    AgentMessage,
    AgentSession,
    AgentToolCall,
)
from wecanfindintern.agent.orchestrator import AgentOrchestrator
from wecanfindintern.agent.tools import AgentDeps, ToolError


class FakeAgentRepo:
    def __init__(self):
        self.sessions = {}
        self.messages = []
        self.tool_calls = []
        self.approvals = []
        self.audit = []

    async def create_session(self, *, title=None):
        session = AgentSession(
            id=uuid4(),
            title=title or "New conversation",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.sessions[session.id] = session
        return session

    async def get_session(self, public_id):
        return self.sessions.get(public_id)

    async def update_session_title(self, public_id, title):
        session = self.sessions.get(public_id)
        if session:
            updated = AgentSession(
                id=session.id,
                title=title,
                created_at=session.created_at,
                updated_at=session.updated_at,
            )
            self.sessions[public_id] = updated
            return updated
        return None

    async def touch_session(self, public_id):
        return None

    async def add_message(self, session_id, role, content, *, token_count=0):
        message = AgentMessage(
            id=uuid4(),
            session_id=session_id,
            role=role,
            content=content,
            created_at=datetime.now(UTC),
        )
        self.messages.append(message)
        return message

    async def list_messages(self, session_id, *, limit=60):
        return [m for m in self.messages if m.session_id == session_id][-limit:]

    async def add_tool_call(
        self,
        *,
        session_id,
        message_id,
        tool_name,
        arguments,
        status="succeeded",
        result=None,
        error=None,
    ):
        call = AgentToolCall(
            id=uuid4(),
            session_id=session_id,
            tool_name=tool_name,
            arguments=arguments,
            status=status,
            result=result,
            error=error,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.tool_calls.append(call)
        return call

    async def list_tool_calls(self, session_id, *, limit=100):
        return [c for c in self.tool_calls if c.session_id == session_id]

    async def create_approval(self, *, session_id, tool_name, arguments, preview):
        approval = AgentApproval(
            id=uuid4(),
            session_id=session_id,
            tool_name=tool_name,
            arguments=arguments,
            preview=preview,
            created_at=datetime.now(UTC),
        )
        self.approvals.append(approval)
        return approval

    async def list_pending_approvals(self, session_id):
        return [
            a
            for a in self.approvals
            if a.session_id == session_id and a.status == "pending"
        ]

    async def get_approval(self, public_id):
        return next((a for a in self.approvals if a.id == public_id), None)

    async def decide_approval(self, public_id, status):
        approval = await self.get_approval(public_id)
        if approval is None or approval.status != "pending":
            return None
        updated = AgentApproval(
            id=approval.id,
            session_id=approval.session_id,
            tool_name=approval.tool_name,
            arguments=approval.arguments,
            preview=approval.preview,
            status=status,
            created_at=approval.created_at,
            decided_at=datetime.now(UTC),
        )
        self.approvals = [updated if a.id == public_id else a for a in self.approvals]
        return updated

    async def append_audit(self, **kwargs):
        self.audit.append(kwargs)

    async def list_sessions(self, *, limit=20):
        return list(self.sessions.values())


class FakeDomain:
    def __init__(self):
        self.bookmarked = []

    async def list_tracked_job_states(self):
        return []

    async def list_tracked_external_states(self):
        return []

    async def get_job(self, job_id):
        return SimpleNamespace(
            id=job_id,
            title="Backend Intern",
            company_name="Acme",
            location=SimpleNamespace(display_name="Toronto"),
            work_mode="hybrid",
            opportunity_type="internship",
            recruiting_term=SimpleNamespace(display_name="Fall 2026"),
            date_posted=None,
            skill_tags=["python"],
            display_tags=[],
            description="Python API work",
        )

    async def bookmark_job(self, job_id):
        self.bookmarked.append(str(job_id))
        return SimpleNamespace(id=uuid4())

    async def list_jobs(self, filters=None, **kwargs):
        if filters is not None:
            return SimpleNamespace(items=[])
        return {"items": [], "total": 0}

    async def list_applications(self, *, query=None, stage=None, **kwargs):
        return [], 0

    async def get_stats(self):
        return SimpleNamespace(model_dump=lambda: {})

    async def get_profile(self):
        from wecanfindintern.profile.models import ProfileBasics, UserProfile

        return UserProfile(
            id=uuid4(),
            schema_version="profile.v1",
            basics=ProfileBasics(full_name="Alex Chen"),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    async def list_jobs_ww(self, **kwargs):
        return {"items": []}

    async def get_job_ww(self, job_id):
        return None


def make_deps(domain=None):
    domain = domain or FakeDomain()
    return AgentDeps(
        job_repo=domain,
        tracker_repo=domain,
        profile_repo=domain,
        waterlooworks=domain,
        llm_config=None,
    )


def make_repo_with_session():
    repo = FakeAgentRepo()
    session = asyncio.run(repo.create_session())
    return repo, session


JOB_ID = str(uuid4())


def test_read_only_turn_records_tool_call_and_reply(monkeypatch):
    repo, session = make_repo_with_session()
    domain = FakeDomain()
    deps = make_deps(domain)

    monkeypatch.setattr(
        orchestrator_module,
        "plan_turn",
        lambda **kwargs: {
            "reply": "planned",
            "tool_calls": [
                {"name": "search_jobs", "arguments": {"query": "python", "limit": 5}}
            ],
        },
    )
    monkeypatch.setattr(
        orchestrator_module,
        "compose_reply",
        lambda **kwargs: "I found no jobs.",
    )

    orchestrator = AgentOrchestrator(repo, deps)
    result = asyncio.run(orchestrator.process_message(session.id, "find python jobs"))
    assert result.message.role == "assistant"
    assert result.message.content == "I found no jobs."
    assert result.pending_approval is None
    assert any(c.tool_name == "search_jobs" for c in repo.tool_calls)
    assert repo.audit


def test_write_turn_creates_approval_without_executing(monkeypatch):
    repo, session = make_repo_with_session()
    domain = FakeDomain()
    deps = make_deps(domain)

    monkeypatch.setattr(
        orchestrator_module,
        "plan_turn",
        lambda **kwargs: {
            "reply": "planned",
            "tool_calls": [
                {
                    "name": "add_interested",
                    "arguments": {"jobs": [{"job_id": JOB_ID, "source": "public"}]},
                }
            ],
        },
    )
    orchestrator = AgentOrchestrator(repo, deps)
    result = asyncio.run(orchestrator.process_message(session.id, "add this job"))
    assert result.pending_approval is not None
    assert result.pending_approval.status == "pending"
    assert domain.bookmarked == []
    assert any(
        c.tool_name == "add_interested" and c.status == "awaiting_approval"
        for c in repo.tool_calls
    )

    decision = asyncio.run(orchestrator.decide_approval(result.pending_approval.id, True))
    assert decision.approval.status == "approved"
    assert domain.bookmarked == [JOB_ID]
    assert "added" in decision.message.content.lower()

    with pytest.raises(ToolError):
        asyncio.run(orchestrator.decide_approval(result.pending_approval.id, True))


def test_denying_approval_does_not_execute(monkeypatch):
    repo, session = make_repo_with_session()
    domain = FakeDomain()
    deps = make_deps(domain)

    monkeypatch.setattr(
        orchestrator_module,
        "plan_turn",
        lambda **kwargs: {
            "reply": "planned",
            "tool_calls": [
                {
                    "name": "add_interested",
                    "arguments": {"jobs": [{"job_id": JOB_ID, "source": "public"}]},
                }
            ],
        },
    )
    orchestrator = AgentOrchestrator(repo, deps)
    result = asyncio.run(orchestrator.process_message(session.id, "add this job"))
    decision = asyncio.run(orchestrator.decide_approval(result.pending_approval.id, False))
    assert decision.approval.status == "denied"
    assert domain.bookmarked == []


def test_keyword_yes_auto_approves(monkeypatch):
    repo, session = make_repo_with_session()
    domain = FakeDomain()
    deps = make_deps(domain)

    monkeypatch.setattr(
        orchestrator_module,
        "plan_turn",
        lambda **kwargs: {
            "reply": "planned",
            "tool_calls": [
                {
                    "name": "add_interested",
                    "arguments": {"jobs": [{"job_id": JOB_ID, "source": "public"}]},
                }
            ],
        },
    )
    orchestrator = AgentOrchestrator(repo, deps)
    asyncio.run(orchestrator.process_message(session.id, "add this job"))
    assert domain.bookmarked == []

    result = asyncio.run(orchestrator.process_message(session.id, "yes"))
    assert domain.bookmarked == [JOB_ID]
    assert "added" in result.message.content.lower()


class FakeMemory:
    def __init__(self):
        self.context_calls = 0
        self.record_turn_calls = 0
        self.scheduled = 0
        self.due = False

    async def build_context(self, session_id, query):
        self.context_calls += 1
        return WorkingContext(
            session_id=session_id,
            summary_text="User prefers Toronto jobs.",
            window=[],
            recalled_memories=[],
            preferences={"TARGET_LOCATIONS": "Toronto"},
            window_token_count=0,
            summary_token_count=8,
            memory_token_count=0,
        )

    async def record_turn(self, session_id):
        self.record_turn_calls += 1
        return self.due

    def schedule_maintenance(self, session_id, deps):
        self.scheduled += 1


def test_memory_context_flows_into_plan_and_maintenance_is_scheduled(monkeypatch):
    repo, session = make_repo_with_session()
    domain = FakeDomain()
    memory = FakeMemory()
    deps = make_deps(domain)
    deps.memory = memory
    deps.llm_config = SimpleNamespace(
        provider="DeepSeek", model_name="deepseek-chat", api_key="x"
    )
    memory.due = True

    captured = {}

    def fake_plan(**kwargs):
        captured.update(kwargs)
        return {"reply": "planned", "tool_calls": []}

    monkeypatch.setattr(orchestrator_module, "plan_turn", fake_plan)

    orchestrator = AgentOrchestrator(repo, deps)
    result = asyncio.run(orchestrator.process_message(session.id, "remember Toronto"))
    assert memory.context_calls == 1
    assert captured["working_context"].summary_text == "User prefers Toronto jobs."
    assert captured["working_context"].preferences["TARGET_LOCATIONS"] == "Toronto"
    assert memory.record_turn_calls == 1
    assert memory.scheduled == 1
    assert result.message.role == "assistant"
