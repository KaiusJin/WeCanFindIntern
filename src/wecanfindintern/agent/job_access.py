"""Canonical Agent read adapters for public and WaterlooWorks jobs."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from wecanfindintern.agent.contracts import AgentDeps, ToolError
from wecanfindintern.agent.models import JobReference
from wecanfindintern.application.job_models import JobListItem
from wecanfindintern.domain.location import clean_location_display
from wecanfindintern.tracker.models import TrackedApplication
from wecanfindintern.waterlooworks.records import (
    waterlooworks_current_application_deadline,
    waterlooworks_salary_text,
)
from wecanfindintern.waterlooworks.taxonomy import resolve_waterloo_opportunity_type


def public_job_summary(
    job: JobListItem, *, description: str | None = None
) -> dict[str, Any]:
    location = job.location.display_name if job.location else None
    return {
        "source": "public",
        "job_id": str(job.id),
        "title": job.title,
        "company": job.company_name,
        "location": location,
        "work_mode": job.work_mode,
        "opportunity_type": job.opportunity_type,
        "recruiting_term": (
            job.recruiting_term.display_name if job.recruiting_term else None
        ),
        "date_posted": job.date_posted.isoformat() if job.date_posted else None,
        "skill_tags": job.skill_tags[:20],
        "requirement_tags": getattr(job, "requirement_tags", []),
        "description": description,
    }


def waterlooworks_job_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "waterloo_work",
        "job_id": item.get("source_job_id"),
        "title": item.get("title"),
        "company": item.get("organization"),
        "division": item.get("division"),
        "location": clean_location_display(item.get("location_text")),
        "work_mode": item.get("work_mode"),
        "opportunity_type": resolve_waterloo_opportunity_type(
            item.get("opportunity_type"), item.get("boards")
        ),
        "date_posted": item.get("date_posted"),
        "application_deadline": waterlooworks_current_application_deadline(item),
        "application_url": item.get("application_url") or item.get("source_url"),
        "salary_text": waterlooworks_salary_text(item),
        "boards": item.get("boards") or [],
        "skill_tags": item.get("skill_tags") or [],
        "requirement_tags": item.get("requirement_tags") or [],
        "description": item.get("description"),
    }


async def resolve_job(ref: JobReference, deps: AgentDeps) -> dict[str, Any] | None:
    if ref.source == "public":
        try:
            job = await deps.job_repo.get_job(UUID(ref.job_id))
        except (ValueError, TypeError):
            raise ToolError(
                "invalid_job_id", f"Invalid public job ID: {ref.job_id}"
            ) from None
        if job is None:
            return None
        return public_job_summary(job, description=job.description)
    item = await deps.waterlooworks.get_job(ref.job_id)
    if item is None:
        return None
    return waterlooworks_job_summary(item)


async def tracked_public_map(deps: AgentDeps) -> dict[str, str]:
    states = await deps.tracker_repo.list_tracked_job_states()
    return {str(state.job_id): state.stage for state in states}


async def tracked_external_map(deps: AgentDeps) -> dict[str, str]:
    rows = await deps.tracker_repo.list_tracked_external_states()
    return {
        row["external_job_id"]: row["stage"]
        for row in rows
        if row.get("source") == "waterloo_work" and row.get("external_job_id")
    }


async def tracked_application_by_ref(
    ref: JobReference, deps: AgentDeps
) -> TrackedApplication | None:
    if ref.source == "public":
        try:
            return await deps.tracker_repo.get_application_for_public_job(UUID(ref.job_id))
        except (TypeError, ValueError):
            raise ToolError(
                "invalid_job_id", f"Invalid public job ID: {ref.job_id}"
            ) from None
    return await deps.tracker_repo.get_application_for_external_job(
        "waterloo_work", ref.job_id
    )
