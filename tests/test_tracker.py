"""Unit tests for Application Tracker models and schemas."""

import asyncio
from datetime import UTC, date, datetime
from uuid import uuid4

from wecanfindintern.tracker.models import (
    ApplicationPriority,
    ApplicationStage,
    TrackedApplication,
    TrackerAnalyticsResponse,
    TrackerBulkUpdateRequest,
    TrackerCreateRequest,
    TrackerEventCreateRequest,
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
        notes="Referred by teammate",
        created_at=now,
        updated_at=now,
    )
    assert app.company_name == "Google"
    assert app.stage == ApplicationStage.INTERESTED
    assert app.notes == "Referred by teammate"


def test_tracker_create_and_update_requests():
    create_req = TrackerCreateRequest(
        company_name="Apple",
        title="iOS Developer Intern",
        stage=ApplicationStage.APPLIED,
        notes="Applied via website",
    )
    assert create_req.company_name == "Apple"
    assert create_req.stage == ApplicationStage.APPLIED

    update_req = TrackerUpdateRequest(
        stage=ApplicationStage.INTERVIEW,
        notes="Round 1 scheduled next Tuesday",
    )
    assert update_req.stage == ApplicationStage.INTERVIEW
    assert "Round 1" in update_req.notes


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
        application_deadline=date(2026, 9, 15),
        follow_up_at=datetime(2026, 9, 1, tzinfo=UTC),
        source=TrackerSource.OTHER,
        priority=ApplicationPriority.HIGH,
        next_step="Ask for an introduction",
    )
    assert request.priority == ApplicationPriority.HIGH
    assert request.source == TrackerSource.OTHER
    assert request.application_deadline == date(2026, 9, 15)

    bulk = TrackerBulkUpdateRequest(
        ids=[uuid4(), uuid4()], stage=ApplicationStage.APPLIED, archive=False
    )
    assert len(bulk.ids) == 2
    assert bulk.stage == ApplicationStage.APPLIED

    event = TrackerEventCreateRequest(title="Recruiter replied")
    assert event.title == "Recruiter replied"


def test_tracker_list_filter_sql_and_params_are_bounded():
    where, params = TrackerRepository._list_where(
        query="waterloo",
        stage=ApplicationStage.INTERVIEW,
        priority=ApplicationPriority.HIGH,
        archived="active",
        attention_only=True,
    )
    assert "archived_at IS NULL" in where
    assert "stage = %s" in where
    assert "priority = %s" in where
    assert "attention" not in where
    assert params[-2:] == ["interview", "high"]


def test_tracker_analytics_contract_includes_v3_insights():
    analytics = TrackerAnalyticsResponse(
        stages=[],
        weekly_applications=[],
        top_companies=[],
        top_locations=[],
        top_sources=[],
        top_categories=[],
        application_to_interview_percent=25.0,
        interview_to_offer_percent=10.0,
    )
    assert analytics.application_to_interview_percent == 25.0
    assert analytics.top_sources == []


def test_tracked_job_ids_support_pool_dict_rows():
    job_id = uuid4()

    class FakeResult:
        async def fetchall(self):
            return [{"job_id": job_id}]

    class FakeConnection:
        async def execute(self, _query):
            return FakeResult()

    class FakeConnectionContext:
        async def __aenter__(self):
            return FakeConnection()

        async def __aexit__(self, *_args):
            return None

    class FakePool:
        def connection(self):
            return FakeConnectionContext()

    result = asyncio.run(TrackerRepository(FakePool()).list_tracked_job_ids())
    assert result == [job_id]


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
    deleted, stage = asyncio.run(TrackerRepository(FakePool({"id": 1, "stage": "interested"})).unbookmark_job(job_id))
    assert deleted is True
    assert stage is None

    # 2. Applied stage -> protected
    deleted, stage = asyncio.run(TrackerRepository(FakePool({"id": 1, "stage": "applied"})).unbookmark_job(job_id))
    assert deleted is False
    assert stage == "applied"

    # 3. Not found -> not deleted
    deleted, stage = asyncio.run(TrackerRepository(FakePool(None)).unbookmark_job(job_id))
    assert deleted is False
    assert stage is None

