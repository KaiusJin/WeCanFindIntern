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


def test_create_custom_application_sql_matches_parameter_count():
    now = datetime.now(UTC)
    public_id = uuid4()

    class FakeCursor:
        def __init__(self):
            self.row = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, query, params):
            assert query.count("%s") == len(params)
            if "INSERT INTO application_tracker (" in query:
                self.row = {
                    "id": public_id,
                    "job_id": None,
                    "external_job_id": None,
                    "company_name": "Example Corp",
                    "title": "Software Engineer Intern",
                    "location_text": "Toronto, ON",
                    "work_mode": "hybrid",
                    "job_url": None,
                    "job_description": None,
                    "salary_text": None,
                    "origin_type": "custom",
                    "source": "other",
                    "stage": "interested",
                    "applied_at": None,
                    "created_at": now,
                    "updated_at": now,
                }

        async def fetchone(self):
            return self.row

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
        def __init__(self):
            self.connection_obj = FakeConnection()

        def connection(self):
            return self.connection_obj

    created = asyncio.run(
        TrackerRepository(FakePool()).create_application(
            TrackerCreateRequest(
                company_name="Example Corp",
                title="Software Engineer Intern",
                location_text="Toronto, ON",
                work_mode="hybrid",
            )
        )
    )
    assert created.id == public_id


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


def test_tracker_application_exposes_clean_location():
    application = TrackedApplication(
        id=uuid4(),
        company_name="Example Corp",
        title="Software Engineer Intern",
        location_text="Toronto, ON, CA",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    assert application.location_text == "Toronto, Ontario, Canada"


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


def test_sync_waterlooworks_application_creates_applied_tracker_record():
    now = datetime.now(UTC)
    public_id = uuid4()

    class FakeCursor:
        def __init__(self):
            self.row = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, query, params):
            assert query.count("%s") == len(params)
            if "SELECT public_id, stage" in query:
                self.row = None
            elif "INSERT INTO application_tracker(" in query:
                self.row = {
                    "id": public_id,
                    "job_id": None,
                    "external_job_id": "471365",
                    "company_name": "Forward Inc",
                    "title": "GTM Engineering",
                    "location_text": "Austin, Texas, United States",
                    "work_mode": "remote",
                    "job_url": None,
                    "job_description": "Build GTM systems.",
                    "salary_text": "CAD 40–60 /hourly",
                    "origin_type": "platform_bookmark",
                    "source": "waterloo_work",
                    "stage": "applied",
                    "applied_at": now,
                    "created_at": now,
                    "updated_at": now,
                }

        async def fetchone(self):
            return self.row

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
        def connection(self):
            return FakeConnection()

    item = asyncio.run(
        TrackerRepository(FakePool()).sync_waterlooworks_application(
            source_job_id="471365",
            company_name="Forward Inc",
            title="GTM Engineering",
            stage=ApplicationStage.APPLIED,
            waterlooworks_status="Applied",
            submitted_at=now,
            location_text="Austin, Texas, United States",
            work_mode="remote",
            job_description="Build GTM systems.",
            salary_text="CAD 40–60 /hourly",
        )
    )
    assert item.external_job_id == "471365"
    assert item.stage == ApplicationStage.APPLIED


def test_sync_waterlooworks_application_preserves_user_stage_and_archive_state():
    now = datetime.now(UTC)
    public_id = uuid4()

    class FakeCursor:
        def __init__(self):
            self.row = None
            self.update_query = ""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, query, params):
            assert query.count("%s") == len(params)
            if "SELECT public_id, stage" in query:
                self.row = {
                    "public_id": public_id,
                    "stage": "offer",
                    "external_stage": "applied",
                    "external_status": "Applied",
                }
            elif "UPDATE application_tracker SET" in query:
                self.update_query = query
                self.row = {
                    "id": public_id,
                    "job_id": None,
                    "external_job_id": "471365",
                    "company_name": "Forward Inc",
                    "title": "GTM Engineering",
                    "location_text": "Austin, Texas, United States",
                    "work_mode": "remote",
                    "job_url": "https://example.test/jobs/471365",
                    "job_description": "Build GTM systems.",
                    "application_deadline": None,
                    "salary_text": "CAD 40.00–60.00 /hourly",
                    "origin_type": "platform_bookmark",
                    "source": "waterloo_work",
                    "stage": "offer",
                    "external_stage": "rejected",
                    "external_status": "Not selected",
                    "applied_at": now,
                    "created_at": now,
                    "updated_at": now,
                }

        async def fetchone(self):
            return self.row

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

    connection = FakeConnection()

    class FakePool:
        def connection(self):
            return connection

    item = asyncio.run(
        TrackerRepository(FakePool()).sync_waterlooworks_application(
            source_job_id="471365",
            company_name="Forward Inc",
            title="GTM Engineering",
            stage=ApplicationStage.REJECTED,
            waterlooworks_status="Not selected",
            submitted_at=now,
            job_url="https://example.test/jobs/471365",
        )
    )

    assert item.stage == ApplicationStage.OFFER
    assert item.external_stage == ApplicationStage.REJECTED
    assert ", stage=%s" not in connection.cursor_obj.update_query
    assert "archived_at" not in connection.cursor_obj.update_query
