"""Public job data API routes."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from wecanfindintern.api.models import (
    JobDetail,
    JobFacetsResponse,
    JobListFilters,
    JobPage,
)
from wecanfindintern.db.read_repository import JobReadRepository

jobs_router = APIRouter(prefix="/api/v1/jobs", tags=["Jobs"])


def _repository(request: Request) -> JobReadRepository:
    return JobReadRepository(request.app.state.database.pool)


RepositoryDependency = Annotated[JobReadRepository, Depends(_repository)]


@jobs_router.get("", response_model=JobPage)
async def list_jobs(
    repo: RepositoryDependency,
    query: str | None = Query(default=None, max_length=200),
    country: str | None = Query(default=None, min_length=2, max_length=2),
    region: str | None = Query(default=None, max_length=32),
    city: str | None = Query(default=None, max_length=120),
    company: str | None = Query(default=None, max_length=160),
    work_mode: str | None = Query(default=None),
    employment_type: str | None = Query(default=None, max_length=40),
    opportunity_type: str | None = Query(default=None, max_length=40),
    schedule_type: str | None = Query(default=None, max_length=40),
    category: str | None = Query(default=None, max_length=60),
    subcategory: str | None = Query(default=None, max_length=60),
    skill: str | None = Query(default=None, max_length=80),
    season: str | None = Query(default=None, max_length=20),
    recruiting_year: int | None = Query(default=None, ge=2020, le=2099),
    recruiting_term: str | None = Query(default=None, max_length=40),
    has_recruiting_term: bool | None = Query(default=None),
    source: str | None = Query(default=None, max_length=40),
    posted_after: str | None = Query(default=None),
    salary_min: str | None = Query(default=None),
    annual_salary_min: str | None = Query(default=None),
    annual_salary_max: str | None = Query(default=None),
    hourly_salary_min: str | None = Query(default=None),
    hourly_salary_max: str | None = Query(default=None),
    has_salary: bool | None = Query(default=None),
    currency: str | None = Query(default=None, min_length=3, max_length=3),
    cursor: str | None = Query(default=None, max_length=256),
    limit: int = Query(default=30, ge=1, le=100),
) -> JobPage:
    try:
        filters = JobListFilters(
            query=query,
            country=country,
            region=region,
            city=city,
            company=company,
            work_mode=work_mode,
            employment_type=employment_type,
            opportunity_type=opportunity_type,
            schedule_type=schedule_type,
            category=category,
            subcategory=subcategory,
            skill=skill,
            season=season,
            recruiting_year=recruiting_year,
            recruiting_term=recruiting_term,
            has_recruiting_term=has_recruiting_term,
            source=source,
            posted_after=posted_after,
            salary_min=salary_min,
            annual_salary_min=annual_salary_min,
            annual_salary_max=annual_salary_max,
            hourly_salary_min=hourly_salary_min,
            hourly_salary_max=hourly_salary_max,
            has_salary=has_salary,
            currency=currency,
            cursor=cursor,
            limit=limit,
        )
        return await repo.list_jobs(filters)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@jobs_router.get("/facets", response_model=JobFacetsResponse)
async def job_facets(repo: RepositoryDependency) -> JobFacetsResponse:
    return await repo.job_facets()


@jobs_router.get("/geo-distribution")
async def geo_distribution(repo: RepositoryDependency) -> dict[str, Any]:
    """Active job counts per U.S. state and Canadian province."""
    regions = await repo.geo_distribution()
    by_country: dict[str, int] = {"US": 0, "CA": 0}
    for region in regions:
        by_country[region["country"]] = (
            by_country.get(region["country"], 0) + region["count"]
        )
    return {
        "regions": regions,
        "total": sum(region["count"] for region in regions),
        "by_country": by_country,
    }


@jobs_router.get("/{job_id}", response_model=JobDetail)
async def get_job(
    job_id: UUID,
    repo: RepositoryDependency,
) -> JobDetail:
    job = await repo.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
