"""Local-only WaterlooWorks browser collection endpoints."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from wecanfindintern.api.dependencies import get_tracker_repository
from wecanfindintern.application.salary_projection import salary_response
from wecanfindintern.application.waterlooworks_tracker import (
    sync_waterlooworks_job_to_tracker,
)
from wecanfindintern.domain.classification import OpportunityType
from wecanfindintern.domain.location import clean_location_display
from wecanfindintern.tracker.repository import TrackerRepository
from wecanfindintern.waterlooworks.models import (
    WaterlooWorksJob,
    WaterlooWorksJobPage,
)
from wecanfindintern.waterlooworks.service import WaterlooWorksService

waterlooworks_router = APIRouter(
    prefix="/api/v1/waterlooworks",
    tags=["WaterlooWorks"],
)


def _to_api_job(item: dict[str, Any]) -> dict[str, Any]:
    """Expose structured salary in the same shape as public job postings."""

    result = dict(item)
    result["location_text"] = clean_location_display(result.get("location_text"))
    result["skill_tags"] = list(result.get("skill_tags") or [])
    minimum = result.get("salary_min")
    maximum = result.get("salary_max")
    interval = result.get("salary_interval")
    salary = salary_response(
        interval=interval,
        minimum=minimum,
        maximum=maximum,
        currency=result.get("salary_currency"),
        source="waterlooworks",
    )
    for key in ("salary_min", "salary_max", "salary_interval", "salary_currency"):
        result.pop(key, None)
    result["salary"] = salary.model_dump(mode="json") if salary else None
    return result


def get_service(request: Request) -> WaterlooWorksService:
    if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="WaterlooWorks import is local-only.")
    return request.app.state.waterlooworks


ServiceDep = Annotated[WaterlooWorksService, Depends(get_service)]
WorkModeFilter = Annotated[
    list[Literal["remote", "hybrid", "onsite", "unknown"]] | None,
    Query(),
]
OpportunityTypeFilter = Annotated[list[OpportunityType] | None, Query()]
BoardFilter = Annotated[list[str] | None, Query()]


@waterlooworks_router.get("/status")
async def status(service: ServiceDep) -> dict[str, Any]:
    return await service.get_status()


@waterlooworks_router.post("/launch")
async def launch(service: ServiceDep) -> dict[str, Any]:
    try:
        return await service.launch()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@waterlooworks_router.post("/collect", status_code=202)
async def collect(service: ServiceDep) -> dict[str, Any]:
    try:
        return await service.start_collection()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@waterlooworks_router.post("/applications/sync", status_code=202)
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


@waterlooworks_router.get("/jobs", response_model=WaterlooWorksJobPage)
async def list_jobs(
    service: ServiceDep,
    board: BoardFilter = None,
    query: str | None = Query(default=None, max_length=200),
    location: str | None = Query(default=None, max_length=200),
    company: str | None = Query(default=None, max_length=160),
    skill: str | None = Query(default=None, max_length=80),
    category: str | None = Query(default=None, max_length=60),
    city: str | None = Query(default=None, max_length=120),
    region: str | None = Query(default=None, max_length=32),
    country: str | None = Query(default=None, max_length=64),
    work_mode: WorkModeFilter = None,
    opportunity_type: OpportunityTypeFilter = None,
    posted_after: date | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=256),
) -> WaterlooWorksJobPage:
    try:
        payload = await service.list_jobs(
            boards=board,
            query=query,
            location=location,
            company=company,
            skill=skill,
            category=category,
            city=city,
            region=region,
            country=country,
            work_modes=work_mode,
            opportunity_types=(
                [value.value for value in opportunity_type]
                if opportunity_type
                else None
            ),
            posted_after=posted_after.isoformat() if posted_after else None,
            limit=limit,
            cursor=cursor,
            include_description=False,
        )
        payload["items"] = [_to_api_job(item) for item in payload["items"]]
        return WaterlooWorksJobPage.model_validate(payload)
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@waterlooworks_router.get("/jobs/{source_job_id}", response_model=WaterlooWorksJob)
async def get_job(
    source_job_id: str,
    service: ServiceDep,
) -> WaterlooWorksJob:
    item = await service.get_job(source_job_id)
    if not item:
        raise HTTPException(status_code=404, detail="WaterlooWorks job not found")
    return WaterlooWorksJob.model_validate(_to_api_job(item))
