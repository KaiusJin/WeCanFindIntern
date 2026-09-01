"""Application-layer adapter between WaterlooWorks and the Tracker."""

from __future__ import annotations

from typing import Any

from wecanfindintern.tracker.models import ApplicationStage, TrackedApplication
from wecanfindintern.tracker.repository import TrackerRepository
from wecanfindintern.waterlooworks.dates import (
    parse_waterlooworks_date,
    parse_waterlooworks_datetime,
)
from wecanfindintern.waterlooworks.records import (
    waterlooworks_current_application_deadline,
    waterlooworks_salary_text,
)


def tracker_stage_for_waterlooworks_status(status: str | None) -> ApplicationStage:
    """Translate a provider-owned status at the application boundary."""

    normalized = " ".join((status or "").split()).casefold()
    return {
        "not selected": ApplicationStage.REJECTED,
        "selected for interview": ApplicationStage.INTERVIEW,
        "employed": ApplicationStage.OFFER,
    }.get(normalized, ApplicationStage.APPLIED)


def waterlooworks_tracker_fields(job: dict[str, Any]) -> dict[str, Any]:
    """Build the common Tracker fields for bookmark and application sync flows."""

    source_job_id = job.get("source_job_id") or job.get("job_id")
    if source_job_id is None:
        raise ValueError("WaterlooWorks job is missing its Job ID")
    return {
        "source_job_id": str(source_job_id),
        "company_name": job.get("organization")
        or job.get("company")
        or "Company not specified",
        "title": job.get("title") or "Untitled role",
        "location_text": job.get("location_text") or job.get("location"),
        "work_mode": job.get("work_mode"),
        "job_url": job.get("application_url") or job.get("source_url"),
        "job_description": job.get("description"),
        # Tracker owns a DATE column, so it receives the Toronto calendar date.
        "application_deadline": parse_waterlooworks_date(
            waterlooworks_current_application_deadline(job)
        ),
        "salary_text": job.get("salary_text") or waterlooworks_salary_text(job),
    }


async def sync_waterlooworks_job_to_tracker(
    repository: TrackerRepository, job: dict[str, Any]
) -> TrackedApplication:
    """Mirror one submitted application without coupling the provider service."""

    status = job.get("application_status") or "Unknown"
    return await repository.sync_waterlooworks_application(
        **waterlooworks_tracker_fields(job),
        stage=tracker_stage_for_waterlooworks_status(status),
        waterlooworks_status=status,
        submitted_at=parse_waterlooworks_datetime(job.get("application_submitted_at")),
    )
