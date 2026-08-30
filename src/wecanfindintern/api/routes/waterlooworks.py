"""Local-only WaterlooWorks browser collection endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from wecanfindintern.domain.location import clean_location_display
from wecanfindintern.domain.normalization import annualize_salary, to_decimal
from wecanfindintern.waterlooworks.service import WaterlooWorksService

waterlooworks_router = APIRouter(
    prefix="/api/v1/waterlooworks",
    tags=["WaterlooWorks"],
)


def _with_salary(item: dict[str, Any]) -> dict[str, Any]:
    """Expose structured salary in the same shape as public job postings."""

    item["location_text"] = clean_location_display(item.get("location_text"))
    minimum = to_decimal(item.get("salary_min"))
    maximum = to_decimal(item.get("salary_max"))
    interval = item.get("salary_interval")
    salary = None
    if minimum is not None or maximum is not None:
        salary = {
            "interval": interval,
            "minimum": minimum,
            "maximum": maximum,
            "currency": item.get("salary_currency"),
            "source": "waterlooworks",
            "annualized_minimum": annualize_salary(minimum, interval),
            "annualized_maximum": annualize_salary(maximum, interval),
        }
    for key in ("salary_min", "salary_max", "salary_interval", "salary_currency"):
        item.pop(key, None)
    item["salary"] = salary
    return item


def get_service(request: Request) -> WaterlooWorksService:
    if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="WaterlooWorks import is local-only.")
    return request.app.state.waterlooworks


ServiceDep = Annotated[WaterlooWorksService, Depends(get_service)]


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


@waterlooworks_router.get("/jobs")
async def list_jobs(
    service: ServiceDep,
    board: str | None = Query(default=None, max_length=40),
    query: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    try:
        payload = await service.list_jobs(
            board=board,
            query=query,
            limit=limit,
            offset=offset,
        )
        payload["items"] = [_with_salary(item) for item in payload["items"]]
        return payload
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@waterlooworks_router.get("/jobs/{source_job_id}")
async def get_job(
    source_job_id: str,
    service: ServiceDep,
) -> dict[str, Any]:
    item = await service.get_job(source_job_id)
    if not item:
        raise HTTPException(status_code=404, detail="WaterlooWorks job not found")
    return _with_salary(item)
