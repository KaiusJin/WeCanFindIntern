"""Route-level contract tests that run without a database."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from wecanfindintern.api.app import app
from wecanfindintern.api.routes.profile import (
    delete_resume,
    export_profile,
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


def test_profile_export_route_returns_profile():
    repo = FakeProfileRepo()
    result = asyncio.run(export_profile(repo=repo))
    assert result.id == repo.profile.id
    assert result.basics.full_name == "Alex Chen"


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


def test_tracker_csv_export_route():
    class FakeTrackerRepo:
        async def list_all_for_export(self, *, query=None, stage=None):
            return []

    response = asyncio.run(export_tracker_csv(repo=FakeTrackerRepo()))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    body = build_tracker_csv([])
    assert body.startswith("Company,Role,Stage,Location,Work mode,Source,Applied at,Salary,Job URL")


def test_openapi_contract_covers_frontend_endpoints():
    spec = app.openapi()
    paths = spec["paths"]

    assert "/api/v1/profile/export" in paths
    assert "get" in paths["/api/v1/profile/resumes"]
    assert "delete" in paths["/api/v1/profile/resumes/{resume_id}"]
    assert "/api/v1/tracker/export.csv" in paths
    assert "patch" in paths["/api/v1/tracker/bulk"]
    assert "get" in paths["/api/v1/tracker/bookmarks/waterlooworks"]
    assert "put" in paths["/api/v1/tracker/bookmarks/waterlooworks/{source_job_id}"]
    assert "delete" in paths["/api/v1/tracker/bookmarks/waterlooworks/{source_job_id}"]
    assert "post" in paths["/api/v1/agent/sessions"]
    assert "post" in paths["/api/v1/agent/sessions/{session_id}/messages"]
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
