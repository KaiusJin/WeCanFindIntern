"""Tool-level tests for the rewritten recommend_jobs pipeline.

Covers the N+1 regression guard (single recall call, no per-job get_job), the
compatibility path for lightweight repositories, tracked-job exclusion, LLM
re-rank application and silent degradation, determinism, and cache behavior.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from wecanfindintern.agent.recommend.cache import recommendation_cache
from wecanfindintern.agent.tools import AgentDeps, LlmConfig, run_tool
from wecanfindintern.llm.gateway import LLMError
from wecanfindintern.profile.models import (
    EducationEntry,
    ProfileBasics,
    SkillEntry,
    UserProfile,
)
from wecanfindintern.tracker.models import TrackerStatsResponse


class RecallJobRepo:
    """Repository exposing the new single-query recall path."""

    def __init__(self, entries):
        # entries: list[(job_like, description)]
        self.entries = entries
        self.recall_calls = 0
        self.last_exclude_ids = None

    async def list_jobs_for_recommendation(self, *, skills, exclude_public_ids, limit):
        self.recall_calls += 1
        self.last_exclude_ids = [str(value) for value in exclude_public_ids]
        excluded = set(self.last_exclude_ids)
        return [
            {
                "item": job,
                "description": description,
                "url": "https://example.com/apply",
                "requirement_tags": getattr(job, "requirement_tags", []),
                "retrieval_sources": ["skill_tags"],
            }
            for job, description in self.entries
            if str(job.id) not in excluded
        ]

    async def list_jobs(self, filters):  # compat path must not run
        raise AssertionError("compat list_jobs used although recall path exists")

    async def get_job(self, job_id):  # N+1 path must not run
        raise AssertionError("per-job get_job used although recall path exists")


class CompatJobRepo:
    """Legacy repository shape: list_jobs + per-job get_job only."""

    def __init__(self, jobs):
        self.jobs = jobs
        self.get_job_calls = 0

    async def list_jobs(self, filters):
        return SimpleNamespace(items=list(self.jobs))

    async def get_job(self, job_id):
        self.get_job_calls += 1
        return next((job for job in self.jobs if job.id == job_id), None)


def make_job(
    *,
    title="Software Engineer Intern",
    company="Acme",
    skills=("python", "fastapi"),
    description="Build APIs with Python and FastAPI.",
    requirement_tags=(),
    work_mode="remote",
    deadline=None,
):
    return SimpleNamespace(
        id=uuid4(),
        title=title,
        company_name=company,
        location=SimpleNamespace(display_name="Waterloo, ON"),
        work_mode=work_mode,
        opportunity_type="internship",
        recruiting_term=SimpleNamespace(display_name="Fall 2026"),
        date_posted=None,
        skill_tags=list(skills),
        display_tags=[],
        description=description,
        requirement_tags=list(requirement_tags),
        application_deadline=deadline,
    )


class FakeTrackerRepo:
    def __init__(self, job_states=None, external_states=None):
        self.job_states = job_states or []
        self.external_states = external_states or []

    async def list_tracked_job_states(self):
        return self.job_states

    async def list_tracked_external_states(self):
        return self.external_states

    async def list_applications(self, *, query=None, stage=None, **kwargs):
        return [], 0

    async def get_stats(self):
        return TrackerStatsResponse()


class FakeProfileRepo:
    def __init__(self, profile):
        self.profile = profile

    async def get_profile(self):
        return self.profile


class FakeWaterlooWorks:
    def __init__(self, jobs):
        self.jobs = jobs

    async def list_jobs(self, *, query=None, limit=50, include_description=False, **kwargs):
        return {"items": [dict(job) for job in self.jobs], "total": len(self.jobs)}


def make_profile(*skills, studying=False):
    return UserProfile(
        id=uuid4(),
        schema_version="profile.v1",
        basics=ProfileBasics(full_name="Alex Chen"),
        education=(
            [EducationEntry(institution="Waterloo", status="studying")]
            if studying
            else []
        ),
        skills=[SkillEntry(name=skill) for skill in skills],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def make_deps(*, job_repo, profile=None, tracker=None, ww=None, llm_config=None):
    return AgentDeps(
        job_repo=job_repo,
        tracker_repo=tracker or FakeTrackerRepo(),
        profile_repo=profile or FakeProfileRepo(make_profile("python")),
        waterlooworks=ww or FakeWaterlooWorks([]),
        llm_config=llm_config,
    )


def run_recommend(deps, **overrides):
    args = {"limit": 5, "source": "public", "use_llm_rerank": False}
    args.update(overrides)
    return asyncio.run(run_tool("recommend_jobs", args, deps, phase="plan"))


@pytest.fixture(autouse=True)
def _clean_cache():
    recommendation_cache.clear()
    yield
    recommendation_cache.clear()


def test_recall_path_avoids_per_job_queries():
    job = make_job()
    repo = RecallJobRepo([(job, "Build APIs with Python.")])
    deps = make_deps(job_repo=repo)
    result = run_recommend(deps)
    assert repo.recall_calls == 1
    assert result["ok"] is True
    recommendations = result["data"]["recommendations"]
    assert [rec["job_id"] for rec in recommendations] == [str(job.id)]
    assert recommendations[0]["application_url"] == "https://example.com/apply"
    assert "python" in recommendations[0]["matched_skills"]
    assert result["data"]["retrieval_mode"] == "skill_fulltext_fallback"


def test_tracked_jobs_are_excluded_via_recall_args():
    job_a = make_job(title="Backend Intern")
    job_b = make_job(title="Platform Intern", company="Beta")
    tracker = FakeTrackerRepo(
        job_states=[
            SimpleNamespace(job_id=job_a.id, application_id=uuid4(), stage="interested")
        ]
    )
    repo = RecallJobRepo([(job_a, "a"), (job_b, "b")])
    deps = make_deps(job_repo=repo, tracker=tracker)
    result = run_recommend(deps)
    assert repo.last_exclude_ids == [str(job_a.id)]
    returned = {rec["job_id"] for rec in result["data"]["recommendations"]}
    assert returned == {str(job_b.id)}

    kept = run_recommend(deps, exclude_tracked=False)
    returned_all = {rec["job_id"] for rec in kept["data"]["recommendations"]}
    assert returned_all == {str(job_a.id), str(job_b.id)}


def test_compat_path_still_works_for_lightweight_repos():
    job = make_job()
    repo = CompatJobRepo([job])
    deps = make_deps(job_repo=repo)
    result = run_recommend(deps)
    assert repo.get_job_calls == 1
    recommendations = result["data"]["recommendations"]
    assert [rec["job_id"] for rec in recommendations] == [str(job.id)]
    # The legacy repo returns a raw object instead of JobDetail, so the tool
    # treats the description as unavailable instead of crashing.
    assert recommendations[0]["description_available"] is False


def test_empty_library_returns_empty_recommendations():
    class EmptyCompatRepo(CompatJobRepo):
        async def list_jobs(self, filters):
            return SimpleNamespace(items=[])

    deps = make_deps(job_repo=EmptyCompatRepo([]))
    result = run_recommend(deps)
    assert result["ok"] is True
    assert result["data"]["recommendations"] == []
    assert result["summary"].startswith("Recommended 0 job(s)")


def test_trap_job_does_not_outrank_real_skill_match():
    go_job = make_job(
        title="Go Developer",
        company="Systems Co",
        skills=("go", "backend"),
        description="Design and build services in Go.",
    )
    google_job = make_job(
        title="Google Cloud Intern",
        company="Google",
        skills=("google cloud", "sre"),
        description="Support Google Cloud customers.",
    )
    deps = make_deps(
        job_repo=RecallJobRepo(
            [(go_job, go_job.description), (google_job, google_job.description)]
        ),
        profile=FakeProfileRepo(make_profile("go")),
    )
    result = run_recommend(deps)
    recommendations = result["data"]["recommendations"]
    assert recommendations[0]["job_id"] == str(go_job.id)
    google_rec = next(rec for rec in recommendations if rec["job_id"] == str(google_job.id))
    assert "go" not in google_rec["matched_skills"]


def test_same_input_produces_same_order():
    jobs = [
        make_job(title=f"Role {index}", company=f"Co{index}", skills=("python",))
        for index in range(4)
    ]
    deps = make_deps(
        job_repo=RecallJobRepo([(job, job.description) for job in jobs])
    )
    first = run_recommend(deps)
    recommendation_cache.clear()
    second = run_recommend(deps)
    assert [rec["job_id"] for rec in first["data"]["recommendations"]] == [
        rec["job_id"] for rec in second["data"]["recommendations"]
    ]


def test_cache_hit_on_repeated_call_and_miss_on_new_args():
    job = make_job()
    deps = make_deps(job_repo=RecallJobRepo([(job, job.description)]))
    first = run_recommend(deps)
    assert first["data"]["cache_hit"] is False
    second = run_recommend(deps)
    assert second["data"]["cache_hit"] is True
    assert second["data"]["recommendations"] == first["data"]["recommendations"]
    different = run_recommend(deps, limit=2)
    assert different["data"]["cache_hit"] is False


def test_llm_rerank_applies_adjustments_and_reasons():
    strong = make_job(title="Python Backend Intern", skills=("python", "fastapi"))
    weaker = make_job(title="Python Support Intern", company="Beta", skills=("python",))
    deps = make_deps(
        job_repo=RecallJobRepo([(strong, strong.description), (weaker, weaker.description)]),
        llm_config=LlmConfig(provider="OpenAI", model_name="gpt-test", api_key="key"),
    )
    payload = SimpleNamespace(
        data={
            "adjustments": [
                {"candidate": 1, "delta": 5, "reason": "Better team-level fit."},
            ]
        }
    )
    with patch(
        "wecanfindintern.agent.recommend.rerank.complete_json", return_value=payload
    ):
        result = run_recommend(deps, use_llm_rerank=True)
    recommendations = result["data"]["recommendations"]
    assert recommendations[0]["job_id"] == str(weaker.id)
    assert any("Better team-level fit." in reason for reason in recommendations[0]["reasons"])
    assert result["data"]["timings_ms"]["llm_rerank"] >= 0
    assert result["data"]["llm_rerank"]["status"] == "applied"


def test_llm_rerank_failure_degrades_to_rule_order():
    strong = make_job(title="Python Backend Intern", skills=("python", "fastapi"))
    weaker = make_job(title="Python Support Intern", company="Beta", skills=("python",))
    deps = make_deps(
        job_repo=RecallJobRepo([(strong, strong.description), (weaker, weaker.description)]),
        llm_config=LlmConfig(provider="OpenAI", model_name="gpt-test", api_key="key"),
    )
    with patch(
        "wecanfindintern.agent.recommend.rerank.complete_json",
        side_effect=LLMError("OpenAI", "boom"),
    ):
        result = run_recommend(deps, use_llm_rerank=True)
    recommendations = result["data"]["recommendations"]
    assert recommendations[0]["job_id"] == str(strong.id)
    assert all("llm_reason" not in rec or not rec.get("llm_reason") for rec in recommendations)
    assert result["data"]["llm_rerank"] == {
        "status": "failed",
        "applied": False,
        "error_type": "LLMError",
    }


def test_student_profile_ranks_internship_above_senior_role():
    senior = make_job(
        title="Senior Python Software Engineer",
        company="Senior Co",
        skills=("python", "fastapi"),
    )
    senior.opportunity_type = "full_time"
    internship = make_job(
        title="Python Software Engineer Intern",
        company="Intern Co",
        skills=("python",),
    )
    deps = make_deps(
        job_repo=RecallJobRepo(
            [(senior, senior.description), (internship, internship.description)]
        ),
        profile=FakeProfileRepo(make_profile("python", studying=True)),
    )
    result = run_recommend(deps)
    assert result["data"]["profile_used"]["early_career"] is True
    assert result["data"]["recommendations"][0]["job_id"] == str(internship.id)


def test_waterloo_source_excludes_tracked_and_filters_expired():
    tracked = {
        "source_job_id": "WW-1",
        "title": "Tracked Role",
        "organization": "Acme",
        "application_deadline": None,
    }
    open_job = {
        "source_job_id": "WW-2",
        "title": "Open Role",
        "organization": "Beta",
        "application_deadline": "2099-01-01",
    }
    expired = {
        "source_job_id": "WW-3",
        "title": "Expired Role",
        "organization": "Gamma",
        "application_deadline": "2000-01-01",
    }
    tracker = FakeTrackerRepo(
        external_states=[
            {
                "source": "waterloo_work",
                "external_job_id": "WW-1",
                "application_id": uuid4(),
                "stage": "interested",
            }
        ]
    )
    deps = make_deps(
        job_repo=CompatJobRepo([]),
        tracker=tracker,
        ww=FakeWaterlooWorks([tracked, open_job, expired]),
    )
    result = run_recommend(deps, source="all")
    returned = {rec["job_id"] for rec in result["data"]["recommendations"]}
    assert returned == {"WW-2"}


def test_waterloo_internship_board_survives_explicit_opportunity_filter():
    internship = {
        "source_job_id": "WW-INTERN",
        "title": "Backend Developer",
        "organization": "Acme",
        "boards": ["full_cycle"],
    }
    deps = make_deps(
        job_repo=CompatJobRepo([]),
        ww=FakeWaterlooWorks([internship]),
    )
    result = run_recommend(
        deps,
        source="waterloo_work",
        opportunity_types=["internship"],
    )
    recommendation = result["data"]["recommendations"][0]
    assert recommendation["job_id"] == "WW-INTERN"
    assert recommendation["opportunity_type"] == "internship"
