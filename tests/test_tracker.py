"""Unit tests for Application Tracker models and schemas."""

from datetime import datetime, UTC
from uuid import uuid4

from wecanfindintern.tracker.models import (
    ApplicationStage,
    TrackedApplication,
    TrackerCreateRequest,
    TrackerStatsResponse,
    TrackerUpdateRequest,
)


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
