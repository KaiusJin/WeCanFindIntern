"""FastAPI application exposing the stable job data contract."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles

from wecanfindintern.api.models import (
    CollectionPlanResponse,
    IngestionRunResponse,
    JobDetail,
    JobFacetsResponse,
    JobListFilters,
    JobPage,
)
from wecanfindintern.config import Settings
from wecanfindintern.db.pool import Database
from wecanfindintern.db.read_repository import JobReadRepository


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    database = Database(settings)
    await database.open()
    app.state.database = database
    yield
    await database.close()


app = FastAPI(
    title="WeCanFindIntern Job Data API",
    version="1.0.0",
    lifespan=lifespan,
)

WEB_DIR = Path(__file__).resolve().parents[3] / "web"


def repository(request: Request) -> JobReadRepository:
    return JobReadRepository(request.app.state.database.pool)


RepositoryDependency = Annotated[JobReadRepository, Depends(repository)]


@app.get("/health")
async def health(request: Request) -> dict[str, str]:
    async with request.app.state.database.pool.connection() as connection:
        await connection.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/api/v1/jobs", response_model=JobPage)
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
    source: str | None = Query(default=None, max_length=40),
    posted_after: str | None = Query(default=None),
    salary_min: str | None = Query(default=None),
    annual_salary_min: str | None = Query(default=None),
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
            source=source,
            posted_after=posted_after,
            salary_min=salary_min,
            annual_salary_min=annual_salary_min,
            has_salary=has_salary,
            currency=currency,
            cursor=cursor,
            limit=limit,
        )
        return await repo.list_jobs(filters)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/v1/jobs/facets", response_model=JobFacetsResponse)
async def job_facets(repo: RepositoryDependency) -> JobFacetsResponse:
    return await repo.job_facets()


@app.get("/api/v1/jobs/{job_id}", response_model=JobDetail)
async def get_job(
    job_id: UUID,
    repo: RepositoryDependency,
) -> JobDetail:
    job = await repo.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/v1/ingestion-runs/{run_id}", response_model=IngestionRunResponse)
async def get_ingestion_run(
    run_id: UUID,
    repo: RepositoryDependency,
) -> IngestionRunResponse:
    run = await repo.get_ingestion_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Ingestion run not found")
    return run


@app.get("/api/v1/collection-plans", response_model=list[CollectionPlanResponse])
async def list_collection_plans(
    repo: RepositoryDependency,
) -> list[CollectionPlanResponse]:
    return await repo.list_collection_plans()


# Keep the browser experience in the same deployable service as the data API.
# API routes are declared first, so the catch-all static route does not shadow them.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
