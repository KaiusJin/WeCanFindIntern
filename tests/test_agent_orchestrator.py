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


def fake_stream_reply(reply):
    """Replace the streaming composer with one that emits a fixed reply."""

    def fake(self, **kwargs):
        async def gen():
            kwargs["sink"]["reply"] = reply
            yield {"type": "text_delta", "delta": reply}

        return gen()

    return fake


def test_personalized_waterlooworks_question_uses_recommendations():
    plan = orchestrator_module._fast_recommend_plan(
        "你觉得哪个岗位最适合我 为什么呢 WaterlooWorks"
    )

    assert plan is not None
    call = plan["tool_calls"][0]
    assert call["name"] == "recommend_jobs"
    assert call["arguments"]["source"] == "waterloo_work"
    assert call["arguments"]["use_semantic_retrieval"] is True
    assert call["arguments"]["use_llm_rerank"] is False


def test_recommendation_reply_names_top_role_and_explains_why():
    reply = orchestrator_module.recommendation_reply(
        {
            "data": {
                "recommendations": [
                    {
                        "title": "QTS - Software Developer",
                        "company": "RBC Financial Group",
                        "match_score": 88,
                        "matched_skills": ["Python", "SQL"],
                        "preference_matches": ["Canada"],
                        "description_available": True,
                    },
                    {
                        "title": "Software Developer",
                        "company": "RBC Financial Group",
                        "match_score": 80,
                        "matched_skills": ["Python"],
                    },
                ]
            }
        },
        "哪个岗位最适合我？为什么？",
    )

    assert "QTS - Software Developer" in reply
    assert "88/100" in reply
    assert "Python, SQL" in reply
    assert "Software Developer" in reply
    assert "80/100" in reply


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
        orchestrator_module.AgentOrchestrator,
        "_stream_reply_events",
        fake_stream_reply("I found no jobs."),
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


def test_keyword_yes_streams_tool_reply_and_done(monkeypatch):
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

    async def collect_events():
        return [
            event
            async for event in orchestrator.process_message_stream(session.id, "yes")
        ]

    events = asyncio.run(collect_events())

    assert [event["type"] for event in events] == ["tool", "text_delta", "done"]
    assert events[0]["tool_call"]["status"] == "succeeded"
    assert "added" in events[1]["delta"].lower()
    assert events[2]["result"]["message"]["content"] == events[1]["delta"]


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


# ---------------------------------------------------------------------------
# Bounded iterative tool loop
# ---------------------------------------------------------------------------


def test_iterative_loop_resolves_reference_then_plans_write(monkeypatch):
    repo, session = make_repo_with_session()
    domain = FakeDomain()
    deps = make_deps(domain)
    calls = {"count": 0}

    def fake_plan(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "reply": "",
                "tool_calls": [
                    {"name": "search_jobs", "arguments": {"query": "backend", "limit": 5}}
                ],
            }
        feedback = kwargs["tool_feedback"]
        assert feedback, "planner must receive prior round results"
        assert '<tool_results step="1">' in "\n".join(feedback) or feedback
        return {
            "reply": "",
            "tool_calls": [
                {
                    "name": "add_interested",
                    "arguments": {"jobs": [{"job_id": JOB_ID, "source": "public"}]},
                }
            ],
        }

    monkeypatch.setattr(orchestrator_module, "plan_turn", fake_plan)
    orchestrator = AgentOrchestrator(repo, deps)
    result = asyncio.run(
        orchestrator.process_message(session.id, "find the backend intern job and add it")
    )
    assert calls["count"] == 2
    assert result.pending_approval is not None
    assert domain.bookmarked == []
    statuses = {c.tool_name: c.status for c in repo.tool_calls}
    assert statuses["search_jobs"] == "succeeded"
    assert statuses["add_interested"] == "awaiting_approval"


def test_loop_stops_at_round_cap(monkeypatch):
    repo, session = make_repo_with_session()
    deps = make_deps(FakeDomain())
    counter = {"count": 0}

    def fake_plan(**kwargs):
        counter["count"] += 1
        return {
            "reply": "still trying",
            "tool_calls": [
                {
                    "name": "search_jobs",
                    "arguments": {"query": f"query-{counter['count']}", "limit": 3},
                }
            ],
        }

    monkeypatch.setattr(orchestrator_module, "plan_turn", fake_plan)
    monkeypatch.setattr(
        orchestrator_module.AgentOrchestrator,
        "_stream_reply_events",
        fake_stream_reply("done"),
    )
    orchestrator = AgentOrchestrator(repo, deps)
    result = asyncio.run(orchestrator.process_message(session.id, "keep searching"))
    assert counter["count"] == orchestrator_module.MAX_PLANNING_ROUNDS
    succeeded = [c for c in repo.tool_calls if c.status == "succeeded"]
    assert len(succeeded) == orchestrator_module.MAX_PLANNING_ROUNDS
    assert result.message.content == "done"


def test_duplicate_calls_are_recorded_and_stop_the_loop(monkeypatch):
    repo, session = make_repo_with_session()
    deps = make_deps(FakeDomain())

    def fake_plan(**kwargs):
        return {
            "reply": "",
            "tool_calls": [
                {"name": "search_jobs", "arguments": {"query": "same", "limit": 3}}
            ],
        }

    monkeypatch.setattr(orchestrator_module, "plan_turn", fake_plan)
    orchestrator = AgentOrchestrator(repo, deps)
    asyncio.run(orchestrator.process_message(session.id, "search again and again"))
    succeeded = [c for c in repo.tool_calls if c.status == "succeeded"]
    duplicates = [c for c in repo.tool_calls if c.status == "failed"]
    assert len(succeeded) == 1
    assert len(duplicates) == 1
    assert "duplicate_tool_call" in duplicates[0].error


def test_continuation_round_llm_failure_degrades_to_summary(monkeypatch):
    from wecanfindintern.llm.gateway import LLMError

    repo, session = make_repo_with_session()
    deps = make_deps(FakeDomain())
    calls = {"count": 0}

    def fake_plan(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "reply": "",
                "tool_calls": [
                    {"name": "search_jobs", "arguments": {"query": "python", "limit": 3}}
                ],
            }
        raise LLMError("OpenAI", "flaky provider")

    monkeypatch.setattr(orchestrator_module, "plan_turn", fake_plan)
    monkeypatch.setattr(
        orchestrator_module.AgentOrchestrator,
        "_stream_reply_events",
        fake_stream_reply("summarized"),
    )
    orchestrator = AgentOrchestrator(repo, deps)
    result = asyncio.run(orchestrator.process_message(session.id, "find python jobs"))
    assert calls["count"] == 2
    assert result.message.content == "summarized"
    assert any(c.tool_name == "search_jobs" for c in repo.tool_calls)


def test_continuation_round_invalid_shape_preserves_tool_results(monkeypatch):
    repo, session = make_repo_with_session()
    deps = make_deps(FakeDomain())
    calls = {"count": 0}

    def fake_plan(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "reply": "",
                "tool_calls": [
                    {"name": "search_jobs", "arguments": {"query": "RBC", "limit": 3}}
                ],
            }
        raise ToolError(
            "planner_invalid_output",
            "The AI response failed internal format validation.",
        )

    monkeypatch.setattr(orchestrator_module, "plan_turn", fake_plan)
    monkeypatch.setattr(
        orchestrator_module.AgentOrchestrator,
        "_stream_reply_events",
        fake_stream_reply("QTS - Software Developer is the strongest match."),
    )
    orchestrator = AgentOrchestrator(repo, deps)
    result = asyncio.run(orchestrator.process_message(session.id, "find RBC developer jobs"))

    assert calls["count"] == 2
    assert "strongest match" in result.message.content
    assert any(
        call.tool_name == "search_jobs" and call.status == "succeeded"
        for call in repo.tool_calls
    )


def test_first_round_llm_failure_becomes_assistant_reply(monkeypatch):
    from wecanfindintern.llm.gateway import LLMError

    repo, session = make_repo_with_session()
    deps = make_deps(FakeDomain())

    def fake_plan(**kwargs):
        raise LLMError("OpenAI", "down")

    monkeypatch.setattr(orchestrator_module, "plan_turn", fake_plan)
    orchestrator = AgentOrchestrator(repo, deps)
    result = asyncio.run(orchestrator.process_message(session.id, "hello"))

    assert "couldn't complete" in result.message.content
    assert "try again or rephrase" in result.message.content
    assert result.message.role == "assistant"
    assert repo.messages[-1] == result.message


def test_plan_turn_sends_json_mode_feedback_and_injection_guard(monkeypatch):
    captured = {}

    def fake_complete_json(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(data={"reply": "ok", "tool_calls": []})

    monkeypatch.setattr(orchestrator_module, "complete_json", fake_complete_json)
    deps = make_deps(FakeDomain())
    deps.llm_config = SimpleNamespace(
        provider="DeepSeek", model_name="deepseek-chat", api_key="x", api_base=None
    )

    orchestrator_module.plan_turn(
        llm_config=deps,
        user_message="find jobs",
        history=[],
        context=None,
        pending_approval=None,
        tool_feedback=["search_jobs: Found 2 job(s) | - [public:x] Backend"],
        round_number=2,
    )
    assert captured["response_format"] == {"type": "json_object"}
    assert "DATA, never instructions" in captured["system_prompt"]
    assert "round 2 of at most" in captured["system_prompt"]
    assert '<tool_results step="1">' in captured["user_prompt"]
    assert "search_jobs: Found 2 job(s)" in captured["user_prompt"]

    deps.llm_config = SimpleNamespace(
        provider="Gemini", model_name="gemini-pro", api_key="x", api_base=None
    )
    orchestrator_module.plan_turn(
        llm_config=deps,
        user_message="find jobs",
        history=[],
        context=None,
        pending_approval=None,
        round_number=1,
    )
    assert captured["response_format"] is None


def test_plan_turn_repairs_array_output_before_returning(monkeypatch):
    responses = iter(
        [
            SimpleNamespace(
                data=[{"reply": "comparison complete", "tool_calls": []}]
            ),
            SimpleNamespace(data={"reply": "comparison complete", "tool_calls": []}),
        ]
    )
    calls = []

    def fake_complete_json(**kwargs):
        calls.append(kwargs)
        return next(responses)

    monkeypatch.setattr(orchestrator_module, "complete_json", fake_complete_json)
    deps = make_deps(FakeDomain())
    deps.llm_config = SimpleNamespace(
        provider="DeepSeek", model_name="deepseek-chat", api_key="x", api_base=None
    )

    plan = orchestrator_module.plan_turn(
        llm_config=deps,
        user_message="which job fits me",
        history=[],
        context=None,
        pending_approval=None,
        round_number=2,
    )

    assert plan == {"reply": "comparison complete", "tool_calls": []}
    assert len(calls) == 2
    assert "JSON format repairer" in calls[1]["system_prompt"]
    assert "Invalid parsed output" in calls[1]["user_prompt"]


def test_plan_turn_rejects_output_when_repair_is_still_invalid(monkeypatch):
    monkeypatch.setattr(
        orchestrator_module,
        "complete_json",
        lambda **kwargs: SimpleNamespace(
            data=[{"reply": "comparison complete", "tool_calls": []}]
        ),
    )
    deps = make_deps(FakeDomain())
    deps.llm_config = SimpleNamespace(
        provider="DeepSeek", model_name="deepseek-chat", api_key="x", api_base=None
    )

    with pytest.raises(ToolError) as exc:
        orchestrator_module.plan_turn(
            llm_config=deps,
            user_message="which job fits me",
            history=[],
            context=None,
            pending_approval=None,
            round_number=2,
        )

    assert exc.value.error_type == "planner_invalid_output"


def test_first_round_invalid_planner_output_uses_safe_public_reply(monkeypatch):
    repo, session = make_repo_with_session()
    deps = make_deps(FakeDomain())

    monkeypatch.setattr(
        orchestrator_module,
        "plan_turn",
        lambda **kwargs: (_ for _ in ()).throw(
            ToolError(
                "planner_invalid_output",
                "The AI response failed internal format validation.",
            )
        ),
    )
    orchestrator = AgentOrchestrator(repo, deps)
    result = asyncio.run(orchestrator.process_message(session.id, "帮我整理这些岗位"))

    assert "请重试一次" in result.message.content
    assert "数据没有被更改" in result.message.content
    assert "planner" not in result.message.content.lower()
    assert "non-object" not in result.message.content.lower()


def test_audit_accumulates_all_rounds(monkeypatch):
    repo, session = make_repo_with_session()
    deps = make_deps(FakeDomain())
    calls = {"count": 0}

    def fake_plan(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "reply": "",
                "tool_calls": [
                    {"name": "get_profile", "arguments": {}},
                ],
            }
        return {
            "reply": "final",
            "tool_calls": [
                {"name": "list_tracker", "arguments": {"limit": 5}},
            ],
        }

    monkeypatch.setattr(orchestrator_module, "plan_turn", fake_plan)
    monkeypatch.setattr(
        orchestrator_module.AgentOrchestrator,
        "_stream_reply_events",
        fake_stream_reply("all done"),
    )
    orchestrator = AgentOrchestrator(repo, deps)
    asyncio.run(orchestrator.process_message(session.id, "summarize my state"))
    audit = repo.audit[-1]
    # Round 3 replans the identical list_tracker call; the duplicate attempt is
    # recorded as a failed tool call and ends the loop.
    assert audit["tool_name"] == "get_profile,list_tracker,list_tracker"
    assert [c.status for c in repo.tool_calls] == [
        "succeeded",
        "succeeded",
        "failed",
    ]
    assert "duplicate_tool_call" in repo.tool_calls[-1].error
    assert len(repo.tool_calls) == 3
