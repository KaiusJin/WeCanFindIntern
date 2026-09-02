"""Unit tests for AI Agent contracts and decision detection."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from wecanfindintern.agent.models import (
    AddIntoTrackerArgs,
    AgentApproval,
    AgentContext,
    AgentJobContext,
    AgentMessage,
    AgentSession,
    AgentToolCall,
    AnalyseJobArgs,
    CompareJobsArgs,
    JobReference,
    RecommendJobsArgs,
    RemoveTrackerArgs,
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
        tool_name="add_into_tracker",
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


def test_analyse_job_accepts_exactly_one_job_reference():
    ref = JobReference(job_id="job-1", source="public")
    assert AnalyseJobArgs(job=ref).job == ref
    assert AnalyseJobArgs(job=ref, response_language="zh").response_language == "zh"


def test_tool_argument_models_validate():
    search = SearchJobsArgs(query="python", source="waterloo_work", limit=5)
    assert search.source == "waterloo_work"

    add = AddIntoTrackerArgs(jobs=[JobReference(job_id="j1", source="public")])
    assert add.jobs[0].display() == "public:j1"

    stage = UpdateTrackerStageArgs(
        application_ids=[str(uuid4())],
        stage=ApplicationStage.INTERVIEW,
    )
    assert stage.stage == ApplicationStage.INTERVIEW

    removal = RemoveTrackerArgs(application_ids=[str(uuid4())])
    assert removal.application_ids


def test_agent_context_accepts_up_to_five_jobs_and_keeps_legacy_job_compatibility():
    jobs = [
        AgentJobContext(id=f"job-{index}", source="public", title=f"Role {index}")
        for index in range(5)
    ]
    assert len(AgentContext(jobs=jobs).jobs) == 5
    assert AgentContext(job=jobs[0]).job.id == "job-0"

    with pytest.raises(ValueError):
        AgentContext(jobs=[*jobs, AgentJobContext(id="job-5")])


def test_compare_jobs_requires_two_to_five_references():
    refs = [JobReference(job_id=f"job-{index}") for index in range(5)]
    assert len(CompareJobsArgs(jobs=refs).jobs) == 5
    with pytest.raises(ValueError):
        CompareJobsArgs(jobs=refs[:1])
    with pytest.raises(ValueError):
        CompareJobsArgs(jobs=[*refs, JobReference(job_id="job-5")])


def test_search_jobs_supports_ranking_filters_and_pagination():
    search = SearchJobsArgs(
        query="backend intern",
        work_modes=["remote", "hybrid"],
        opportunity_types=["internship"],
        recruiting_terms=["Fall 2026"],
        posted_after="2026-08-01",
        public_cursor="public-cursor",
        waterloo_cursor="waterloo-cursor",
        limit=50,
    )
    assert search.posted_after.isoformat() == "2026-08-01"
    assert search.public_cursor == "public-cursor"
    assert search.waterloo_cursor == "waterloo-cursor"
    assert search.limit == 50


def test_recommend_jobs_uses_low_latency_ranking_by_default():
    assert RecommendJobsArgs().use_semantic_retrieval is True
    assert RecommendJobsArgs().use_llm_rerank is False


def test_decision_detection_keywords():
    assert _detect_decision("yes") is True
    assert _detect_decision("确认") is True
    assert _detect_decision("go ahead") is True
    assert _detect_decision("no") is False
    assert _detect_decision("取消") is False
    assert _detect_decision("show me my interested jobs") is None
    assert _detect_decision("yes, add them all") is True
    assert _detect_decision("not now") is False
