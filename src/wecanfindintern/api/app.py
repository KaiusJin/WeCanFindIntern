"""FastAPI application factory exposing the stable job data contract and web UI."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from wecanfindintern.agent.memory.manager import AgentMemoryManager
from wecanfindintern.agent.memory.store import AgentMemoryStore
from wecanfindintern.agent.recommend.embeddings import EmbeddingConfig, EmbeddingGateway
from wecanfindintern.agent.recommend.indexer import RecommendationIndexer
from wecanfindintern.api.routes.agent import agent_router
from wecanfindintern.api.routes.ats import ats_router
from wecanfindintern.api.routes.cover_letter import cover_letter_router
from wecanfindintern.api.routes.interview import interview_router
from wecanfindintern.api.routes.jobs import jobs_router
from wecanfindintern.api.routes.profile import profile_router
from wecanfindintern.api.routes.resumes import resumes_router
from wecanfindintern.api.routes.tracker import tracker_router
from wecanfindintern.api.routes.waterlooworks import waterlooworks_router
from wecanfindintern.config import Settings
from wecanfindintern.db.pool import Database
from wecanfindintern.llm import cache as llm_cache
from wecanfindintern.waterlooworks import WaterlooWorksService

WEB_DIR = Path(
    os.getenv("WCFI_WEB_DIR", str(Path(__file__).resolve().parents[3] / "web"))
)
logger = logging.getLogger(__name__)

DESKTOP_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; media-src 'self' blob:; "
    "font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'"
)


async def _recommendation_index_loop(
    indexer: RecommendationIndexer,
    waterlooworks: WaterlooWorksService,
    wake: asyncio.Event,
) -> None:
    """Keep the derived lexical RAG index fresh without delaying API startup."""

    # A document-version bump must refresh unchanged active jobs as well as jobs
    # arriving through the ingestion trigger queue.
    await indexer.enqueue_stale_public_documents()
    iteration = 0
    while True:
        try:
            report = await indexer.index_pending(limit=100)
            if iteration % 10 == 0:
                waterloo_cursor: str | None = None
                while True:
                    waterloo_page = await waterlooworks.list_jobs(
                        limit=500,
                        cursor=waterloo_cursor,
                        include_description=True,
                    )
                    await indexer.index_waterloo_jobs(waterloo_page["items"])
                    waterloo_cursor = waterloo_page.get("next_cursor")
                    if not waterloo_page.get("has_more") or not waterloo_cursor:
                        break
            # Document writes and embedding generation are separate retryable stages.
            # This also repairs vectors left missing by a previous transient failure.
            await indexer.embed_missing_primary_chunks(limit=100)
            iteration += 1
            delay = 2 if report.scanned >= 100 else 30
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Recommendation index maintenance failed: %s", error)
            delay = 30
        try:
            await asyncio.wait_for(wake.wait(), timeout=delay)
        except TimeoutError:
            pass
        finally:
            wake.clear()


def create_app() -> FastAPI:
    """Build the application with its routes, dependencies and static mount."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = Settings.from_env()
        llm_cache.configure(settings.database_url)
        database = Database(settings)
        await database.open()
        app.state.database = database
        app.state.agent_memory = AgentMemoryManager(AgentMemoryStore(database.pool))
        app.state.waterlooworks = WaterlooWorksService()
        embedding_config = EmbeddingConfig.from_env()
        app.state.recommendation_indexer = RecommendationIndexer(
            database.pool,
            embedder=(
                EmbeddingGateway(embedding_config)
                if embedding_config is not None
                else None
            ),
        )
        app.state.recommendation_index_wake = asyncio.Event()
        app.state.background_collection = None
        if os.getenv("WCFI_USER_DATA_DIR") and os.getenv("WCFI_RESOURCE_DIR"):
            from wecanfindintern.desktop.collection import BackgroundCollectionService
            from wecanfindintern.desktop.runtime import DesktopPaths

            desktop_paths = DesktopPaths.from_env()
            app.state.background_collection = BackgroundCollectionService(
                config_path=desktop_paths.collection_plan,
                lock_path=desktop_paths.runtime / "collection-campaign.lock",
            )
            app.state.background_collection.start()
        recommendation_index_task = asyncio.create_task(
            _recommendation_index_loop(
                app.state.recommendation_indexer,
                app.state.waterlooworks,
                app.state.recommendation_index_wake,
            )
        )
        yield
        if app.state.background_collection is not None:
            await app.state.background_collection.stop()
        recommendation_index_task.cancel()
        with suppress(asyncio.CancelledError):
            await recommendation_index_task
        await app.state.agent_memory.shutdown()
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
    app.include_router(jobs_router)
    app.include_router(tracker_router)
    app.include_router(profile_router)
    app.include_router(resumes_router)
    app.include_router(waterlooworks_router)
    app.include_router(agent_router)

    @app.get("/health")
    async def health(request: Request) -> dict[str, str]:
        async with request.app.state.database.pool.connection() as connection:
            await connection.execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/desktop/status")
    async def desktop_status(request: Request) -> dict:
        service = request.app.state.background_collection
        collection = (
            service.status.payload()
            if service is not None
            else {"enabled": False, "running": False}
        )
        return {"status": "ready", "collection": collection}

    @app.post("/desktop/collection/run", status_code=202)
    async def run_desktop_collection(request: Request) -> dict:
        service = request.app.state.background_collection
        if service is None or not service.status.enabled:
            raise HTTPException(status_code=409, detail="Background collection is disabled.")
        if not service.trigger():
            raise HTTPException(status_code=409, detail="Collection is already running.")
        return {"accepted": True}

    # Keep the browser experience in the same deployable service as the data API.
    # API routes are declared first, so the catch-all static route does not shadow them.
    @app.middleware("http")
    async def frontend_cache_headers(request: Request, call_next):
        desktop_token = os.getenv("WCFI_DESKTOP_TOKEN")
        if desktop_token:
            from wecanfindintern.desktop.security import enforce_desktop_token

            response = await enforce_desktop_token(
                request, call_next, expected_token=desktop_token
            )
        else:
            response = await call_next(request)
        path = request.url.path
        response.headers["Content-Security-Policy"] = DESKTOP_CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if path in ("/", "/index.html"):
            # The entry document must always revalidate so new deployments are
            # picked up immediately.
            response.headers["Cache-Control"] = "no-store"
        elif (
            path.startswith("/modules/")
            or path.startswith("/vendor/")
            or path == "/styles.css"
        ):
            # Static assets: short client cache; StaticFiles emits ETags, so
            # expired entries revalidate with a 304 instead of a full download.
            response.headers["Cache-Control"] = "public, max-age=300"
        return response

    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


app = create_app()
