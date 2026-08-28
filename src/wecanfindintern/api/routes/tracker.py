"""FastAPI routes for the application tracker workspace."""

from __future__ import annotations

import csv
import io
import math
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from wecanfindintern.tracker.models import (
    ApplicationStage,
    TrackedApplication,
    TrackedJobState,
    TrackerBulkDeleteRequest,
    TrackerBulkResult,
    TrackerBulkUpdateRequest,
    TrackerCreateRequest,
    TrackerEvent,
    TrackerListResponse,
    TrackerUpdateRequest,
)
from wecanfindintern.tracker.repository import TrackerRepository

tracker_router = APIRouter(prefix="/api/v1/tracker", tags=["Application Tracker"])


def get_tracker_repo(request: Request) -> TrackerRepository:
    return TrackerRepository(request.app.state.database.pool)


TrackerRepoDep = Annotated[TrackerRepository, Depends(get_tracker_repo)]


@tracker_router.get("", response_model=TrackerListResponse)
async def list_tracked_applications(
    repo: TrackerRepoDep,
    query: str | None = Query(default=None, max_length=200),
    stage: ApplicationStage | None = None,
    sort: Literal[
        "updated", "created", "applied", "company"
    ] = "updated",
    direction: Literal["asc", "desc"] = "desc",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=100),
) -> TrackerListResponse:
    items, total = await repo.list_applications(
        query=query,
        stage=stage,
        sort=sort,
        direction=direction,
        page=page,
        page_size=page_size,
    )
    return TrackerListResponse(
        items=items,
        stats=await repo.get_stats(),
        total=total,
        page=page,
        page_size=page_size,
        total_pages=math.ceil(total / page_size) if total else 0,
    )


@tracker_router.get("/bookmarks", response_model=list[TrackedJobState])
async def get_tracked_job_states(repo: TrackerRepoDep) -> list[TrackedJobState]:
    return await repo.list_tracked_job_states()


@tracker_router.put("/bookmarks/{job_id}", response_model=TrackedApplication)
async def bookmark_platform_job(job_id: UUID, repo: TrackerRepoDep) -> TrackedApplication:
    item = await repo.bookmark_job(job_id)
    if not item:
        raise HTTPException(status_code=404, detail="Job not found")
    return item


@tracker_router.delete("/bookmarks/{job_id}")
async def unbookmark_platform_job(job_id: UUID, repo: TrackerRepoDep) -> dict[str, Any]:
    deleted, stage = await repo.unbookmark_job(job_id)
    if deleted:
        return {"success": True, "deleted": True}
    if stage:
        return {
            "success": False,
            "deleted": False,
            "protected": True,
            "stage": stage,
            "message": (
                f"This application is currently in stage '{stage}' and is protected "
                "in your Tracker."
            ),
        }
    raise HTTPException(status_code=404, detail="Tracked job not found")


@tracker_router.get("/export.csv")
async def export_tracker_csv(
    repo: TrackerRepoDep,
    query: str | None = Query(default=None, max_length=200),
    stage: ApplicationStage | None = None,
) -> StreamingResponse:
    items = await repo.list_all_for_export(query=query, stage=stage)
    return StreamingResponse(
        iter([build_tracker_csv(items)]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=applications.csv"},
    )


def build_tracker_csv(items: list[TrackedApplication]) -> str:
    """Serialize tracked applications into the exported CSV text."""

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Company",
            "Role",
            "Stage",
            "Location",
            "Work mode",
            "Source",
            "Applied at",
            "Salary",
            "Job URL",
        ]
    )
    for item in items:
        writer.writerow(
            [
                item.company_name,
                item.title,
                item.stage.value,
                item.location_text or "",
                item.work_mode or "",
                item.source.value,
                item.applied_at.isoformat() if item.applied_at else "",
                item.salary_text or "",
                item.job_url or "",
            ]
        )
    return output.getvalue()


@tracker_router.patch("/bulk", response_model=TrackerBulkResult)
async def bulk_update_applications(
    payload: TrackerBulkUpdateRequest, repo: TrackerRepoDep
) -> TrackerBulkResult:
    if payload.stage is None:
        raise HTTPException(status_code=422, detail="No bulk update was specified")
    updated = await repo.bulk_update(
        payload.ids, stage=payload.stage
    )
    return TrackerBulkResult(updated=updated)


@tracker_router.delete("/bulk", response_model=TrackerBulkResult)
async def bulk_delete_applications(
    repo: TrackerRepoDep, payload: Annotated[TrackerBulkDeleteRequest, Body()]
) -> TrackerBulkResult:
    deleted = await repo.bulk_delete(payload.ids)
    return TrackerBulkResult(deleted=deleted)


@tracker_router.post("", response_model=TrackedApplication, status_code=201)
async def create_tracked_application(
    payload: TrackerCreateRequest, repo: TrackerRepoDep
) -> TrackedApplication:
    return await repo.create_application(payload)


@tracker_router.get("/{application_id}", response_model=TrackedApplication)
async def get_tracked_application(application_id: UUID, repo: TrackerRepoDep) -> TrackedApplication:
    item = await repo.get_application(application_id)
    if not item:
        raise HTTPException(status_code=404, detail="Tracked application not found")
    return item


@tracker_router.patch("/{application_id}", response_model=TrackedApplication)
async def update_tracked_application(
    application_id: UUID, payload: TrackerUpdateRequest, repo: TrackerRepoDep
) -> TrackedApplication:
    try:
        updated = await repo.update_application(application_id, payload)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not updated:
        raise HTTPException(status_code=404, detail="Tracked application not found")
    return updated


@tracker_router.delete("/{application_id}")
async def delete_tracked_application(application_id: UUID, repo: TrackerRepoDep) -> dict[str, bool]:
    if not await repo.delete_application(application_id):
        raise HTTPException(status_code=404, detail="Tracked application not found")
    return {"ok": True}


@tracker_router.get("/{application_id}/events", response_model=list[TrackerEvent])
async def list_tracker_events(application_id: UUID, repo: TrackerRepoDep) -> list[TrackerEvent]:
    if not await repo.get_application(application_id):
        raise HTTPException(status_code=404, detail="Tracked application not found")
    return await repo.list_events(application_id)
