"""Unit tests for Application Tracker models and schemas."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from wecanfindintern.tracker.models import (
    ApplicationStage,
    TrackedApplication,
    TrackerBulkUpdateRequest,
    TrackerCreateRequest,
    TrackerSource,
    TrackerStatsResponse,
    TrackerUpdateRequest,
)
from wecanfindintern.tracker.repository import TrackerRepository


def test_tracker_models():
    item_id = uuid4()
    job_id = uuid4()
    now = datetime.now(UTC)

    app = TrackedApplication(
        id=item_id,
        job_id=job_id,
        company_name="Google",
        title="Software Engineering Intern",
        location_text="Waterloo, ON",
        work_mode="hybrid",
        job_url="https://careers.google.com/jobs/123",
        salary_text="$55/hr",
        stage=ApplicationStage.INTERESTED,
        created_at=now,
        updated_at=now,
    )
    assert app.company_name == "Google"
    assert app.stage == ApplicationStage.INTERESTED


def test_tracker_create_and_update_requests():
    create_req = TrackerCreateRequest(
        company_name="Apple",
        title="iOS Developer Intern",
        stage=ApplicationStage.APPLIED,
    )
    assert create_req.company_name == "Apple"
    assert create_req.stage == ApplicationStage.APPLIED

    update_req = TrackerUpdateRequest(
        stage=ApplicationStage.INTERVIEW,
    )
    assert update_req.stage == ApplicationStage.INTERVIEW


def test_tracker_stats():
    stats = TrackerStatsResponse(
        total=10,
        interested_count=3,
        applied_count=4,
        interview_count=2,
        offer_count=1,
        rejected_count=0,
        response_rate_percent=42.9,
    )
    assert stats.total == 10
    assert stats.response_rate_percent == 42.9


def test_tracker_v3_fields_and_bulk_contract():
    request = TrackerCreateRequest(
        company_name="OpenAI",
        title="Research Intern",
        source=TrackerSource.OTHER,
    )
    assert request.source == TrackerSource.OTHER

    bulk = TrackerBulkUpdateRequest(
        ids=[uuid4(), uuid4()], stage=ApplicationStage.APPLIED
    )
    assert len(bulk.ids) == 2
    assert bulk.stage == ApplicationStage.APPLIED

def test_tracker_list_filter_sql_and_params_are_bounded():
    where, params = TrackerRepository._list_where(
        query="waterloo",
        stage=ApplicationStage.INTERVIEW,
    )
    assert "archived_at IS NULL" in where
    assert "stage = %s" in where
    assert "attention" not in where
    assert params[-1:] == ["interview"]


def test_unbookmark_job_safe_interested_and_protected_states():
    job_id = uuid4()

    class FakeCursor:
        def __init__(self, row):
            self._row = row
            self.executed_delete = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, query, params):
            if "DELETE" in query:
                self.executed_delete = True

        async def fetchone(self):
            return self._row

    class FakeConnection:
        def __init__(self, row):
            self._row = row
            self.cursor_obj = FakeCursor(row)

        def cursor(self, row_factory=None):
            return self.cursor_obj

        def transaction(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class FakePool:
        def __init__(self, row):
            self._row = row

        def connection(self):
            return FakeConnection(self._row)

    # 1. Interested stage -> safely deleted
    deleted, stage = asyncio.run(
        TrackerRepository(FakePool({"id": 1, "stage": "interested"})).unbookmark_job(job_id)
    )
    assert deleted is True
    assert stage is None

    # 2. Applied stage -> protected
    deleted, stage = asyncio.run(
        TrackerRepository(FakePool({"id": 1, "stage": "applied"})).unbookmark_job(job_id)
    )
    assert deleted is False
    assert stage == "applied"

    # 3. Not found -> not deleted
    deleted, stage = asyncio.run(TrackerRepository(FakePool(None)).unbookmark_job(job_id))
    assert deleted is False
    assert stage is None


def test_bookmark_waterlooworks_job_includes_salary():
    class FakeCursor:
        def __init__(self):
            self.executed = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, query, params):
            self.executed.append((query, params))

        async def fetchone(self):
            return None

    class FakeConnection:
        def __init__(self):
            self.cursor_obj = FakeCursor()

        def cursor(self, row_factory=None):
            return self.cursor_obj

        def transaction(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class FakePool:
        def __init__(self, connection):
            self._connection = connection

        def connection(self):
            return self._connection

    connection = FakeConnection()
    repo = TrackerRepository(FakePool(connection))
    asyncio.run(
        repo.bookmark_waterlooworks_job(
            source_job_id="123456",
            company_name="Acme Corp",
            title="Software Developer",
            salary_text="$40/hr",
        )
    )
    query, params = connection.cursor_obj.executed[0]
    assert "salary_text" in query
    assert "$40/hr" in params
    assert "waterloo_work" in query
    assert params[0] == "123456"
