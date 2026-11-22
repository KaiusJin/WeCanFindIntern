"""List and detail endpoints for locally collected WaterlooWorks jobs."""

from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query

from wecanfindintern.application.salary_projection import salary_response
from wecanfindintern.domain.classification import OpportunityType
from wecanfindintern.domain.location import clean_location_display
from wecanfindintern.waterlooworks.features.browse_jobs.schemas import (
    WaterlooWorksJob,
    WaterlooWorksJobPage,
)
from wecanfindintern.waterlooworks.features.dependencies import ServiceDep

router = APIRouter()

WorkModeFilter = Annotated[
    list[Literal["remote", "hybrid", "onsite", "unknown"]] | None,
    Query(),
]
OpportunityTypeFilter = Annotated[list[OpportunityType] | None, Query()]
BoardFilter = Annotated[list[str] | None, Query()]


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


@router.get("/jobs", response_model=WaterlooWorksJobPage)
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
                [value.value for value in opportunity_type] if opportunity_type else None
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


@router.get("/jobs/{source_job_id}", response_model=WaterlooWorksJob)
async def get_job(
    source_job_id: str,
    service: ServiceDep,
) -> WaterlooWorksJob:
    item = await service.get_job(source_job_id)
    if not item:
        raise HTTPException(status_code=404, detail="WaterlooWorks job not found")
    return WaterlooWorksJob.model_validate(_to_api_job(item))
