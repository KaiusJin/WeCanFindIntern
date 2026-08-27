"""FastAPI router for Application Tracker (local machine database storage)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from wecanfindintern.tracker.models import (
    TrackedApplication,
    TrackerCreateRequest,
    TrackerListResponse,
    TrackerStatsResponse,
    TrackerUpdateRequest,
)
from wecanfindintern.tracker.repository import TrackerRepository

tracker_router = APIRouter(prefix="/api/v1/tracker", tags=["Application Tracker"])


def get_tracker_repo(request: Request) -> TrackerRepository:
    return TrackerRepository(request.app.state.database.pool)


TrackerRepoDep = Annotated[TrackerRepository, Depends(get_tracker_repo)]


@tracker_router.get("", response_model=TrackerListResponse)
async def list_tracked_applications(repo: TrackerRepoDep) -> TrackerListResponse:
    """List all tracked job applications and summary funnel statistics."""
    items = await repo.list_applications()
    stats = await repo.get_stats()
    return TrackerListResponse(items=items, stats=stats)


@tracker_router.get("/stats", response_model=TrackerStatsResponse)
async def get_tracker_stats(repo: TrackerRepoDep) -> TrackerStatsResponse:
    """Retrieve funnel statistics for tracked applications."""
    return await repo.get_stats()


@tracker_router.post("", response_model=TrackedApplication, status_code=201)
async def create_tracked_application(
    payload: TrackerCreateRequest, repo: TrackerRepoDep
) -> TrackedApplication:
    """Add a job to personal application tracker (from search or custom entry)."""
    return await repo.create_application(payload)


@tracker_router.get("/{application_id}", response_model=TrackedApplication)
async def get_tracked_application(
    application_id: UUID, repo: TrackerRepoDep
) -> TrackedApplication:
    """Get a single tracked job application by UUID."""
    item = await repo.get_application(application_id)
    if not item:
        raise HTTPException(status_code=404, detail="Tracked application not found")
    return item


@tracker_router.patch("/{application_id}", response_model=TrackedApplication)
async def update_tracked_application(
    application_id: UUID, payload: TrackerUpdateRequest, repo: TrackerRepoDep
) -> TrackedApplication:
    """Update application stage, notes, dates, or details."""
    updated = await repo.update_application(application_id, payload)
    if not updated:
        raise HTTPException(status_code=404, detail="Tracked application not found")
    return updated


@tracker_router.delete("/{application_id}")
async def delete_tracked_application(
    application_id: UUID, repo: TrackerRepoDep
) -> dict[str, bool]:
    """Delete a tracked application from the database."""
    deleted = await repo.delete_application(application_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Tracked application not found")
    return {"ok": True}
