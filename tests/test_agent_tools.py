"""Deterministic tool-layer tests using fake repositories."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from wecanfindintern.agent.tools import (
    AgentDeps,
    ToolError,
    _merge_profile,
    run_tool,
)
from wecanfindintern.profile.models import (
    ProfileBasics,
    ProfilePayload,
    SkillEntry,
    UserProfile,
)
from wecanfindintern.tracker.models import TrackerStatsResponse


def make_job(
    *,
    job_id=None,
    title="Software Engineer Intern",
    company="Acme",
    skills=("python", "fastapi"),
    description="Build APIs with Python and FastAPI.",
):
    return SimpleNamespace(
        id=job_id or uuid4(),
        title=title,
        company_name=company,
        location=SimpleNamespace(display_name="Toronto, ON"),
        work_mode="hybrid",
        opportunity_type="internship",
        recruiting_term=SimpleNamespace(display_name="Fall 2026"),
        date_posted=None,
        skill_tags=list(skills),
        display_tags=[],
        description=description,
    )


class FakeJobRepo:
    def __init__(self, jobs=None):
        self.jobs = jobs or []
        self.last_filters = None

    async def list_jobs(self, filters):
        self.last_filters = filters
        items = self.jobs
        if filters.query:
            lowered = filters.query.lower()
            items = [
                job
                for job in items
                if lowered in job.title.lower()
                or lowered in (job.description or "").lower()
            ]
        if filters.company:
            items = [job for job in items if job.company_name == filters.company]
        if filters.skill:
            items = [job for job in items if filters.skill.lower() in job.skill_tags]
        return SimpleNamespace(items=items)

    async def get_job(self, job_id):
        return next((job for job in self.jobs if job.id == job_id), None)


class FakeTrackerRepo:
    def __init__(self):
        self.job_states = []
        self.external_states = []
        self.applications = []
        self.deleted_public = []
        self.deleted_external = []
        self.bookmarked = []
        self.bulk_updated = []

    async def list_tracked_job_states(self):
        return self.job_states

    async def list_tracked_external_states(self):
        return self.external_states

    async def get_application(self, public_id):
        return next((app for app in self.applications if app.id == public_id), None)

    async def list_applications(self, *, query=None, stage=None, **kwargs):
        return self.applications, len(self.applications)

    async def get_stats(self):
        return TrackerStatsResponse()

    async def bookmark_job(self, job_id):
        self.bookmarked.append(("public", str(job_id)))
        return SimpleNamespace(id=uuid4())

    async def bookmark_waterlooworks_job(self, **kwargs):
        self.bookmarked.append(("waterloo_work", kwargs["source_job_id"]))
        return SimpleNamespace(id=uuid4())

    async def unbookmark_job(self, job_id):
        key = str(job_id)
        self.deleted_public.append(key)
        stage = next((s.stage for s in self.job_states if str(s.job_id) == key), None)
        if stage is None:
            return False, None
        if stage != "interested":
            return False, stage
        self.job_states = [s for s in self.job_states if str(s.job_id) != key]
        return True, None

    async def unbookmark_waterlooworks_job(self, source_job_id):
        self.deleted_external.append(source_job_id)
        stage = next(
            (
                s["stage"]
                for s in self.external_states
                if s["external_job_id"] == source_job_id
            ),
            None,
        )
        if stage is None:
            return False, None
        if stage != "interested":
            return False, stage
        self.external_states = [
            s for s in self.external_states if s["external_job_id"] != source_job_id
        ]
        return True, None

    async def bulk_update(self, ids, *, stage):
        self.bulk_updated.extend(ids)
        return len(ids)


class FakeProfileRepo:
    def __init__(self, profile=None, saved=None):
        self.profile = profile or _empty_profile()
        self.saved = saved

    async def get_profile(self):
        return self.profile

    async def save_profile(self, payload):
        self.saved = payload
        return UserProfile(
            id=uuid4(),
            **payload.model_dump(),
            completion_percent=80,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )


class FakeWaterlooWorks:
    def __init__(self, jobs=None):
        self.jobs = jobs or []

    async def list_jobs(self, *, query=None, limit=50, include_description=False, **kwargs):
        items = [
            {
                "source_job_id": job["source_job_id"],
                "title": job["title"],
                "organization": job.get("organization"),
                "division": job.get("division"),
                "location_text": job.get("location_text"),
                "city": job.get("city"),
                "province": job.get("province"),
                "country": job.get("country"),
                "work_mode": job.get("work_mode", "unknown"),
                "date_posted": job.get("date_posted"),
                "application_deadline": job.get("application_deadline"),
                "application_url": job.get("application_url"),
                "boards": job.get("boards", []),
                "description": job.get("description") if include_description else None,
            }
            for job in self.jobs
        ]
        return {"items": items, "total": len(items)}

    async def get_job(self, source_job_id):
        return next(
            (job for job in self.jobs if job["source_job_id"] == source_job_id), None
        )


def _empty_profile():
    return UserProfile(
        id=uuid4(),
        schema_version="profile.v1",
        basics=ProfileBasics(full_name="Alex Chen"),
        skills=[SkillEntry(name="python"), SkillEntry(name="fastapi")],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _deps(*, job_repo=None, tracker=None, profile=None, ww=None):
    return AgentDeps(
        job_repo=job_repo or FakeJobRepo(),
        tracker_repo=tracker or FakeTrackerRepo(),
        profile_repo=profile or FakeProfileRepo(),
        waterlooworks=ww or FakeWaterlooWorks(),
        llm_config=None,
    )


def test_search_jobs_public_and_waterloo():
    job = make_job(title="Python Backend Intern", company="Acme")
    ww_job = {
        "source_job_id": "WW-42",
        "title": "Data Science Intern",
        "organization": "Shopify",
    }
    deps = _deps(job_repo=FakeJobRepo([job]), ww=FakeWaterlooWorks([ww_job]))
    result = asyncio.run(
        run_tool(
            "search_jobs",
            {"query": "python", "source": "all", "limit": 10},
            deps,
            phase="plan",
        )
    )
    assert result["ok"] is True
    assert result["data"]["public"][0]["title"] == "Python Backend Intern"
    assert result["data"]["waterloo_work"][0]["job_id"] == "WW-42"
    assert "Found" in result["summary"]


def test_search_jobs_passes_public_filters_and_relevance_sort():
    repo = FakeJobRepo([make_job(title="Python Backend Intern", company="Acme")])
    deps = _deps(job_repo=repo)
    result = asyncio.run(
        run_tool(
            "search_jobs",
            {
                "query": "python",
                "company": "Acme",
                "work_modes": ["hybrid"],
                "opportunity_types": ["internship"],
                "recruiting_terms": ["Fall 2026"],
                "posted_after": "2026-08-01",
                "source": "public",
                "limit": 7,
            },
            deps,
            phase="plan",
        )
    )
    filters = repo.last_filters
    assert filters.query == "python"
    assert filters.company == "Acme"
    assert filters.work_modes == ["hybrid"]
    assert filters.opportunity_types == ["internship"]
    assert filters.recruiting_terms == ["Fall 2026"]
    assert filters.posted_after.isoformat() == "2026-08-01"
    assert filters.sort_by_relevance is True
    assert filters.limit == 7
    assert result["pagination"]["public"]["has_more"] is False


def test_search_jobs_filters_waterloo_metadata_consistently():
    matching = {
        "source_job_id": "WW-MATCH",
        "title": "Backend Developer",
        "organization": "Acme Labs",
        "location_text": "Toronto, ON, Canada",
        "city": "Toronto",
        "province": "ON",
        "country": "CA",
        "work_mode": "remote",
        "date_posted": "2026-08-20",
        "boards": ["full_cycle"],
    }
    wrong_mode = {**matching, "source_job_id": "WW-WRONG", "work_mode": "onsite"}
    deps = _deps(ww=FakeWaterlooWorks([matching, wrong_mode]))
    result = asyncio.run(
        run_tool(
            "search_jobs",
            {
                "company": "Acme",
                "city": "Toronto",
                "country": "CA",
                "region": "ON",
                "work_modes": ["remote"],
                "opportunity_types": ["internship"],
                "posted_after": "2026-08-01",
                "source": "waterloo_work",
            },
            deps,
            phase="plan",
        )
    )
    assert [item["job_id"] for item in result["data"]["waterloo_work"]] == [
        "WW-MATCH"
    ]
    assert result["data"]["waterloo_work"][0]["opportunity_type"] == "internship"


def test_get_job_details_missing_raises():
    deps = _deps()
    with pytest.raises(ToolError) as exc:
        asyncio.run(
            run_tool(
                "get_job_details",
                {"job_id": str(uuid4()), "source": "public"},
                deps,
                phase="plan",
            )
        )
    assert exc.value.error_type == "job_not_found"


def test_add_interested_plan_and_execute_idempotent():
    job = make_job(title="Backend Intern")
    tracker = FakeTrackerRepo()
    deps = _deps(job_repo=FakeJobRepo([job]), tracker=tracker)
    args = {"jobs": [{"job_id": str(job.id), "source": "public"}]}

    planned = asyncio.run(run_tool("add_interested", args, deps, phase="plan"))
    assert planned["requires_approval"] is True
    assert planned["preview"]["jobs"][0]["already_tracked"] is False

    executed = asyncio.run(run_tool("add_interested", args, deps, phase="execute"))
    assert executed["data"]["results"][0]["status"] == "added"
    assert tracker.bookmarked == [("public", str(job.id))]

    tracker.job_states = [
        SimpleNamespace(job_id=job.id, application_id=uuid4(), stage="interested")
    ]
    executed_again = asyncio.run(run_tool("add_interested", args, deps, phase="execute"))
    assert executed_again["data"]["results"][0]["status"] == "already_interested"
    assert tracker.bookmarked == [("public", str(job.id))]


def test_add_interested_waterlooworks_job():
    ww_job = {
        "source_job_id": "WW-7",
        "title": "QA Intern",
        "organization": "Rivian",
    }
    tracker = FakeTrackerRepo()
    deps = _deps(tracker=tracker, ww=FakeWaterlooWorks([ww_job]))
    executed = asyncio.run(
        run_tool(
            "add_interested",
            {"jobs": [{"job_id": "WW-7", "source": "waterloo_work"}]},
            deps,
            phase="execute",
        )
    )
    assert executed["data"]["results"][0]["status"] == "added"
    assert ("waterloo_work", "WW-7") in tracker.bookmarked


def test_update_tracker_stage_skips_unchanged():
    app_id = uuid4()
    tracker = FakeTrackerRepo()
    tracker.applications = [
        SimpleNamespace(
            id=app_id,
            company_name="Acme",
            title="Backend Intern",
            stage=SimpleNamespace(value="interested"),
        )
    ]
    deps = _deps(tracker=tracker)

    planned = asyncio.run(
        run_tool(
            "update_tracker_stage",
            {"application_ids": [str(app_id)], "stage": "applied"},
            deps,
            phase="plan",
        )
    )
    assert planned["requires_approval"] is True
    assert planned["preview"]["records"][0]["current_stage"] == "interested"

    executed = asyncio.run(
        run_tool(
            "update_tracker_stage",
            {"application_ids": [str(app_id)], "stage": "applied"},
            deps,
            phase="execute",
        )
    )
    assert executed["data"]["results"][0]["status"] == "updated"
    assert tracker.bulk_updated == [app_id]

    tracker.bulk_updated = []
    executed = asyncio.run(
        run_tool(
            "update_tracker_stage",
            {"application_ids": [str(app_id)], "stage": "interested"},
            deps,
            phase="execute",
        )
    )
    assert executed["data"]["results"][0]["status"] == "unchanged"
    assert tracker.bulk_updated == []


def test_update_tracker_stage_missing_targets_returns_gracefully():
    deps = _deps(tracker=FakeTrackerRepo())
    result = asyncio.run(
        run_tool(
            "update_tracker_stage",
            {"application_ids": [str(uuid4())], "stage": "applied"},
            deps,
            phase="plan",
        )
    )
    assert result["ok"] is True
    assert "No matching tracker records" in result["summary"]


def test_remove_interested_protects_applied_records():
    job = make_job(title="Backend Intern")
    tracker = FakeTrackerRepo()
    tracker.job_states = [
        SimpleNamespace(job_id=job.id, application_id=uuid4(), stage="applied")
    ]
    deps = _deps(job_repo=FakeJobRepo([job]), tracker=tracker)
    executed = asyncio.run(
        run_tool(
            "remove_interested",
            {"jobs": [{"job_id": str(job.id), "source": "public"}]},
            deps,
            phase="execute",
        )
    )
    result = executed["data"]["results"][0]
    assert result["status"] == "protected"
    assert result["stage"] == "applied"
    assert tracker.deleted_public == []


def test_remove_interested_interested_is_removed():
    job = make_job(title="Backend Intern")
    tracker = FakeTrackerRepo()
    tracker.job_states = [
        SimpleNamespace(job_id=job.id, application_id=uuid4(), stage="interested")
    ]
    deps = _deps(job_repo=FakeJobRepo([job]), tracker=tracker)
    executed = asyncio.run(
        run_tool(
            "remove_interested",
            {"jobs": [{"job_id": str(job.id), "source": "public"}]},
            deps,
            phase="execute",
        )
    )
    assert executed["data"]["results"][0]["status"] == "removed"
    assert tracker.deleted_public == [str(job.id)]


def test_recommend_jobs_ranks_by_profile_skills():
    python_job = make_job(
        title="Python Intern",
        company="Acme",
        skills=("python", "fastapi"),
    )
    java_job = make_job(
        title="Java Developer",
        company="Other",
        skills=("java", "spring"),
        description="Build backend services in Java.",
    )
    profile = _empty_profile()
    deps = _deps(
        job_repo=FakeJobRepo([python_job, java_job]),
        profile=FakeProfileRepo(profile),
    )
    result = asyncio.run(
        run_tool(
            "recommend_jobs",
            {"limit": 5, "source": "public"},
            deps,
            phase="plan",
        )
    )
    recs = result["data"]["recommendations"]
    assert recs[0]["job_id"] == str(python_job.id)
    assert "python" in recs[0]["matched_skills"]
    assert recs[0]["reasons"]
    assert result["summary"].startswith("Recommended")


def test_update_profile_plan_shows_diff_and_no_changes_is_safe():
    current = _empty_profile()
    proposed_payload = ProfilePayload(
        basics=ProfileBasics(full_name="Alex Chen", email="alex@example.com"),
    )
    deps = _deps(profile=FakeProfileRepo(profile=current))
    planned = asyncio.run(
        run_tool(
            "update_profile",
            {"payload": proposed_payload.model_dump(mode="json")},
            deps,
            phase="plan",
        )
    )
    assert planned["requires_approval"] is True
    assert any(c["field"] == "email" for c in planned["preview"]["changes"])

    no_change = asyncio.run(
        run_tool(
            "update_profile",
            {"payload": current.model_dump(mode="json")},
            deps,
            phase="plan",
        )
    )
    assert no_change["ok"] is True
    assert "No profile changes" in no_change["summary"]


def test_update_profile_merge_preserves_untouched_sections():
    current = _empty_profile()
    partial = ProfilePayload(
        basics=ProfileBasics(email="alex.chen@example.com"),
    )
    merged = _merge_profile(current, partial)
    assert merged.basics.email == "alex.chen@example.com"
    assert merged.basics.full_name == "Alex Chen"
    assert [s.name for s in merged.skills] == ["python", "fastapi"]


def test_update_profile_partial_payload_plans_and_executes():
    current = _empty_profile()
    repo = FakeProfileRepo(profile=current)
    deps = _deps(profile=repo)
    args = {
        "payload": {
            "basics": {"email": "alex.chen@example.com", "city": "Toronto"}
        }
    }
    planned = asyncio.run(run_tool("update_profile", args, deps, phase="plan"))
    assert planned["requires_approval"] is True
    fields = {c["field"] for c in planned["preview"]["changes"]}
    assert {"email", "city"} <= fields

    executed = asyncio.run(run_tool("update_profile", args, deps, phase="execute"))
    assert executed["data"]["changes"]
    assert repo.saved.basics.email == "alex.chen@example.com"
    assert repo.saved.basics.city == "Toronto"


def test_update_profile_rejects_malformed_payload_structure():
    deps = _deps(profile=FakeProfileRepo(profile=_empty_profile()))
    with pytest.raises(ToolError) as exc:
        asyncio.run(
            run_tool(
                "update_profile",
                {"payload": {"email": "a@b.com", "city": "Toronto"}},
                deps,
                phase="plan",
            )
        )
    assert exc.value.error_type == "invalid_profile"


# ---------------------------------------------------------------------------
# generate_interview_questions tool
# ---------------------------------------------------------------------------


def test_generate_interview_questions_resolves_job_description():
    from unittest.mock import patch

    from wecanfindintern.agent.tools import LlmConfig
    from wecanfindintern.interview.models import InterviewQuestionItem
    from wecanfindintern.interview.service import InterviewQuestionsResponse

    job = make_job(
        title="Backend Intern",
        skills=("python",),
        description="Build Python APIs with FastAPI and PostgreSQL.",
    )
    deps = _deps(job_repo=FakeJobRepo([job]))
    deps.llm_config = LlmConfig(provider="OpenAI", model_name="gpt-4o", api_key="key")

    fake_response = InterviewQuestionsResponse(
        ok=True,
        questions=[
            InterviewQuestionItem(
                id=1,
                category="behavioral",
                category_label="Behavioral",
                question="Describe a project you led.",
            )
        ],
    )
    with patch(
        "wecanfindintern.interview.service.generate_interview_questions",
        return_value=fake_response,
    ) as mock_generate:
        result = asyncio.run(
            run_tool(
                "generate_interview_questions",
                {"job_id": str(job.id), "source": "public"},
                deps,
                phase="plan",
            )
        )
    assert mock_generate.call_args.kwargs["job_description"].startswith("Build Python APIs")
    assert result["ok"] is True
    assert result["data"]["questions"][0]["question"] == "Describe a project you led."
    assert "Backend Intern" in result["summary"]


def test_generate_interview_questions_requires_job_or_description():
    from wecanfindintern.agent.tools import LlmConfig

    deps = _deps()
    deps.llm_config = LlmConfig(provider="OpenAI", model_name="gpt-4o", api_key="key")
    with pytest.raises(ToolError) as exc:
        asyncio.run(
            run_tool("generate_interview_questions", {}, deps, phase="plan")
        )
    assert exc.value.error_type == "invalid_arguments"


def test_generate_interview_questions_uses_raw_description():
    from unittest.mock import patch

    from wecanfindintern.agent.tools import LlmConfig
    from wecanfindintern.interview.models import InterviewQuestionItem
    from wecanfindintern.interview.service import InterviewQuestionsResponse

    deps = _deps()
    deps.llm_config = LlmConfig(provider="Ollama", model_name="llama3", api_key="")
    fake_response = InterviewQuestionsResponse(
        ok=True,
        questions=[
            InterviewQuestionItem(
                id=1,
                category="technical",
                category_label="Technical",
                question="Explain REST idempotency.",
            )
        ],
    )
    with patch(
        "wecanfindintern.interview.service.generate_interview_questions",
        return_value=fake_response,
    ) as mock_generate:
        result = asyncio.run(
            run_tool(
                "generate_interview_questions",
                {"job_description": "REST API intern role."},
                deps,
                phase="plan",
            )
        )
    assert mock_generate.call_args.kwargs["provider"] == "Ollama"
    assert result["ok"] is True
    assert result["data"]["job"] == "the provided description"


def test_generate_interview_questions_requires_profile_context():
    from datetime import UTC, datetime

    from wecanfindintern.agent.tools import LlmConfig
    from wecanfindintern.profile.models import ProfileBasics, UserProfile

    class EmptyProfileRepo:
        async def get_profile(self):
            return UserProfile(
                id=uuid4(),
                schema_version="profile.v1",
                basics=ProfileBasics(),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )

    deps = _deps(profile=EmptyProfileRepo())
    deps.llm_config = LlmConfig(provider="OpenAI", model_name="gpt-4o", api_key="key")
    with pytest.raises(ToolError) as exc:
        asyncio.run(
            run_tool(
                "generate_interview_questions",
                {"job_description": "Backend intern role."},
                deps,
                phase="plan",
            )
        )
    assert exc.value.error_type == "profile_missing"
