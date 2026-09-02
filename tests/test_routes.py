"""Route-level contract tests that run without a database."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from fastapi import FastAPI

from wecanfindintern.agent.tools import ToolError
from wecanfindintern.api.app import app
from wecanfindintern.api.models import JobPage
from wecanfindintern.api.routes.agent import _public_agent_error, delete_agent_session
from wecanfindintern.api.routes.jobs import _repository, jobs_router
from wecanfindintern.api.routes.profile import (
    delete_resume,
    list_resumes,
)
from wecanfindintern.api.routes.tracker import build_tracker_csv, export_tracker_csv
from wecanfindintern.profile.models import ProfileBasics, UserProfile


def _profile() -> UserProfile:
    return UserProfile(
        id=uuid4(),
        schema_version="profile.v1",
        basics=ProfileBasics(full_name="Alex Chen"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_agent_llm_errors_hide_internal_details():
    status, detail = _public_agent_error(
        ToolError("llm_failed", "Agent planner returned a non-object response.")
    )

    assert status == 502
    assert detail == "The AI model could not complete this request. Please try again."
    assert "planner" not in detail.lower()
    assert "non-object" not in detail.lower()


class FakeProfileRepo:
    def __init__(self, profile=None, resumes=None, deleted=True):
        self.profile = profile or _profile()
        self.resumes = resumes or []
        self.deleted = deleted

    async def get_profile(self):
        return self.profile

    async def list_resumes(self):
        return self.resumes

    async def delete_resume(self, resume_id):
        return self.deleted


def test_profile_resume_routes():
    repo = FakeProfileRepo(deleted=True)
    assert asyncio.run(list_resumes(repo=repo)) == []
    assert asyncio.run(delete_resume(uuid4(), repo=repo)) == {"ok": True}

    missing = FakeProfileRepo(deleted=False)
    from fastapi import HTTPException

    try:
        asyncio.run(delete_resume(uuid4(), repo=missing))
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("expected HTTPException for missing resume")


def test_agent_session_delete_route():
    class FakeAgentRepo:
        def __init__(self, deleted):
            self.deleted = deleted

        async def delete_session(self, _session_id):
            return self.deleted

    assert asyncio.run(delete_agent_session(uuid4(), repo=FakeAgentRepo(True))) == {"deleted": True}

    from fastapi import HTTPException

    try:
        asyncio.run(delete_agent_session(uuid4(), repo=FakeAgentRepo(False)))
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("expected HTTPException for missing agent session")


def test_tracker_csv_export_route():
    class FakeTrackerRepo:
        async def list_all_for_export(self, *, query=None, stage=None):
            return []

    response = asyncio.run(export_tracker_csv(repo=FakeTrackerRepo()))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    body = build_tracker_csv([])
    assert body.startswith(
        "Company,Role,Stage,Location,Work mode,Source,Applied at,"
        "Application deadline,External status,Salary,Job URL"
    )


def test_openapi_contract_covers_frontend_endpoints():
    spec = app.openapi()
    paths = spec["paths"]

    assert "get" in paths["/api/v1/profile/resumes"]
    assert "delete" in paths["/api/v1/profile/resumes/{resume_id}"]
    assert "/api/v1/tracker/export.csv" in paths
    assert "patch" in paths["/api/v1/tracker/bulk"]
    assert "get" in paths["/api/v1/tracker/bookmarks/waterlooworks"]
    assert "put" in paths["/api/v1/tracker/bookmarks/waterlooworks/{source_job_id}"]
    assert "delete" in paths["/api/v1/tracker/bookmarks/waterlooworks/{source_job_id}"]
    assert "post" in paths["/api/v1/waterlooworks/applications/sync"]
    assert "post" in paths["/api/v1/agent/sessions"]
    assert "delete" in paths["/api/v1/agent/sessions/{session_id}"]
    assert "post" in paths["/api/v1/agent/sessions/{session_id}/messages"]
    assert "get" in paths["/api/v1/agent/sessions/{session_id}/tool-calls"]
    assert "post" in paths["/api/v1/agent/approvals/{approval_id}/decision"]

    analyze = paths["/api/v1/interview/analyze"]["post"]["requestBody"]["content"][
        "multipart/form-data"
    ]["schema"]
    schema_ref = analyze["$ref"].split("/")[-1]
    properties = spec["components"]["schemas"][schema_ref]["properties"]
    assert "audio_file" in properties
    assert "post" in paths["/api/v1/interview/sessions"]
    assert "get" in paths["/api/v1/interview/sessions"]
    assert "get" in paths["/api/v1/interview/sessions/{session_id}"]
    assert "delete" in paths["/api/v1/interview/sessions/{session_id}"]
    assert "get" in paths["/api/v1/interview/trend"]


def test_jobs_route_accepts_repeated_multi_value_filters():
    class FakeJobsRepo:
        filters = None

        async def list_jobs(self, filters):
            self.filters = filters
            return JobPage(items=[], next_cursor=None, has_more=False)

    repo = FakeJobsRepo()
    test_app = FastAPI()
    test_app.include_router(jobs_router)
    test_app.dependency_overrides[_repository] = lambda: repo

    async def request_jobs():
        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(
                "/api/v1/jobs",
                params=[
                    ("country", "CA"),
                    ("country", "US"),
                    ("region", "ON,CA"),
                    ("region", "NY,US"),
                    ("work_mode", "hybrid"),
                    ("work_mode", "remote"),
                ],
            )

    response = asyncio.run(request_jobs())

    assert response.status_code == 200
    assert repo.filters.countries == ["CA", "US"]
    assert repo.filters.regions == ["ON,CA", "NY,US"]
    assert repo.filters.work_modes == ["hybrid", "remote"]
