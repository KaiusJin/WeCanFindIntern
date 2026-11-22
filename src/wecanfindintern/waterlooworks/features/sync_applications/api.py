"""Start synchronization of WaterlooWorks submitted applications."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from wecanfindintern.api.dependencies import get_tracker_repository
from wecanfindintern.tracker.repository import TrackerRepository
from wecanfindintern.waterlooworks.features.dependencies import ServiceDep
from wecanfindintern.waterlooworks.integrations.tracker import sync_waterlooworks_job_to_tracker

router = APIRouter()


@router.post("/applications/sync", status_code=202)
async def sync_applications(
    service: ServiceDep,
    tracker: Annotated[TrackerRepository, Depends(get_tracker_repository)],
) -> dict[str, Any]:
    try:

        async def sync_one(job: dict[str, Any]) -> object:
            return await sync_waterlooworks_job_to_tracker(tracker, job)

        return await service.start_application_sync(sync_one)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
