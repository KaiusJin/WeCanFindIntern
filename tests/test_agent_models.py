"""Unit tests for AI Agent contracts and decision detection."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from wecanfindintern.agent.models import (
    AddInterestedArgs,
    AgentApproval,
    AgentMessage,
    AgentSession,
    AgentToolCall,
    JobReference,
    SearchJobsArgs,
    UpdateTrackerStageArgs,
)
from wecanfindintern.agent.orchestrator import _detect_decision
from wecanfindintern.tracker.models import ApplicationStage


def test_agent_session_and_message_models():
    session = AgentSession(
        id=uuid4(),
        title="Hello",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    message = AgentMessage(
        id=uuid4(),
        session_id=session.id,
        role="user",
        content="hi",
        created_at=datetime.now(UTC),
    )
    assert session.title == "Hello"
    assert message.role == "user"


def test_agent_tool_call_and_approval_models():
    now = datetime.now(UTC)
    tool = AgentToolCall(
        id=uuid4(),
        session_id=uuid4(),
        tool_name="search_jobs",
        arguments={"query": "python"},
        status="succeeded",
        result={"ok": True},
        created_at=now,
        updated_at=now,
    )
    assert tool.status == "succeeded"
    approval = AgentApproval(
        id=uuid4(),
        session_id=uuid4(),
        tool_name="add_interested",
        arguments={"jobs": []},
        preview={"count": 0},
        created_at=now,
    )
    assert approval.status == "pending"


def test_job_reference_display():
    ref = JobReference(job_id=" abc-123 ", source="waterloo_work")
    assert ref.job_id == "abc-123"
    assert ref.display() == "ww:abc-123"
    assert JobReference(job_id="x").display() == "public:x"


def test_tool_argument_models_validate():
    search = SearchJobsArgs(query="python", source="waterloo_work", limit=5)
    assert search.source == "waterloo_work"

    add = AddInterestedArgs(
        jobs=[JobReference(job_id="j1", source="public")]
    )
    assert add.jobs[0].display() == "public:j1"

    stage = UpdateTrackerStageArgs(
        application_ids=[str(uuid4())],
        stage=ApplicationStage.INTERVIEW,
    )
    assert stage.stage == ApplicationStage.INTERVIEW


def test_decision_detection_keywords():
    assert _detect_decision("yes") is True
    assert _detect_decision("确认") is True
    assert _detect_decision("go ahead") is True
    assert _detect_decision("no") is False
    assert _detect_decision("取消") is False
    assert _detect_decision("show me my interested jobs") is None
    assert _detect_decision("yes, add them all") is True
    assert _detect_decision("not now") is False
