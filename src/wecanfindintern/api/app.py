"""FastAPI application factory exposing the stable job data contract and web UI."""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles

from wecanfindintern.agent.recommend.embeddings import EmbeddingConfig, EmbeddingGateway
from wecanfindintern.agent.recommend.indexer import RecommendationIndexer
from wecanfindintern.api.models import (
    JobDetail,
    JobFacetsResponse,
    JobListFilters,
    JobPage,
)
from wecanfindintern.api.routes.agent import agent_router
from wecanfindintern.api.routes.ats import ats_router
from wecanfindintern.api.routes.cover_letter import cover_letter_router
from wecanfindintern.api.routes.interview import interview_router
from wecanfindintern.api.routes.profile import profile_router
from wecanfindintern.api.routes.tracker import tracker_router
from wecanfindintern.api.routes.waterlooworks import waterlooworks_router
from wecanfindintern.config import Settings
from wecanfindintern.db.pool import Database
from wecanfindintern.db.read_repository import JobReadRepository
from wecanfindintern.waterlooworks import WaterlooWorksService

WEB_DIR = Path(__file__).resolve().parents[3] / "web"
logger = logging.getLogger(__name__)


async def _recommendation_index_loop(
    database: Database, waterlooworks: WaterlooWorksService
) -> None:
    """Keep the derived lexical RAG index fresh without delaying API startup."""

    embedding_config = EmbeddingConfig.from_env()
    indexer = RecommendationIndexer(
        database.pool,
        embedder=(
            EmbeddingGateway(embedding_config) if embedding_config is not None else None
        ),
    )
    iteration = 0
    while True:
        try:
            report = await indexer.index_pending(limit=100)
            if iteration % 10 == 0:
                waterloo_page = await waterlooworks.list_jobs(
                    limit=10000, include_description=True
                )
                await indexer.index_waterloo_jobs(waterloo_page["items"])
            iteration += 1
            await asyncio.sleep(2 if report.scanned >= 100 else 30)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Recommendation index maintenance failed: %s", error)
            await asyncio.sleep(30)


def create_app() -> FastAPI:
    """Build the application with its routes, dependencies and static mount."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = Settings.from_env()
        database = Database(settings)
        await database.open()
        app.state.database = database
        app.state.waterlooworks = WaterlooWorksService()
        recommendation_index_task = asyncio.create_task(
            _recommendation_index_loop(database, app.state.waterlooworks)
        )
        yield
        recommendation_index_task.cancel()
        with suppress(asyncio.CancelledError):
            await recommendation_index_task
        await app.state.waterlooworks.close()
        await database.close()

    app = FastAPI(
        title="WeCanFindIntern Job Data API",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.include_router(ats_router)
    app.include_router(interview_router)
    app.include_router(cover_letter_router)
    app.include_router(tracker_router)
    app.include_router(profile_router)
    app.include_router(waterlooworks_router)
    app.include_router(agent_router)

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

    # Keep the browser experience in the same deployable service as the data API.
    # API routes are declared first, so the catch-all static route does not shadow them.
    @app.middleware("http")
    async def no_cache_frontend(request: Request, call_next):
        response = await call_next(request)
        if (
            request.url.path in ("/", "/index.html")
            or request.url.path.startswith("/modules/")
            or request.url.path == "/styles.css"
        ):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


app = create_app()
