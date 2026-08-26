"""Durable JobSpy plan execution with per-source checkpoints and retries."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from wecanfindintern.db.ingestion_repository import JobIngestionRepository
from wecanfindintern.domain.jobs import canonical_job_from_jobspy
from wecanfindintern.ingestion.jobspy_adapter import JobSpyQuery, scrape_and_normalize
from wecanfindintern.ingestion.location_query import resolve_query_location
from wecanfindintern.scheduler.models import CollectionCheckpoint, CollectionPlan
from wecanfindintern.scheduler.repository import CollectionRepository


class CollectionRunner:
    def __init__(
        self,
        collection_repository: CollectionRepository,
        ingestion_repository: JobIngestionRepository,
        *,
        worker_id: str | None = None,
        lease_seconds: int = 900,
    ) -> None:
        self.collection_repository = collection_repository
        self.ingestion_repository = ingestion_repository
        self.worker_id = worker_id or f"worker-{uuid4()}"
        self.lease_seconds = lease_seconds

    async def run_once(self) -> bool:
        plan = await self.collection_repository.claim_due_plan(
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if plan is None:
            return False

        run_id = plan.active_run_id
        if run_id is None:
            run = await self.ingestion_repository.start_run(
                sources=plan.sites,
                query={"plan": plan.name, **plan.query},
                collection_plan_id=plan.id,
            )
            run_id = run.internal_id
            await self.collection_repository.attach_run(
                plan_id=plan.id,
                worker_id=self.worker_id,
                run_id=run_id,
            )

        await self.collection_repository.initialize_checkpoints(
            plan_id=plan.id,
            run_id=run_id,
            sites=plan.sites,
        )
        checkpoints = await self.collection_repository.checkpoints(plan.id)
        await self._execute_sources(plan, run_id, checkpoints)
        return True

    async def _execute_sources(
        self,
        plan: CollectionPlan,
        run_id: int,
        checkpoints: list[CollectionCheckpoint],
    ) -> None:
        retry_times: list[datetime] = []
        for checkpoint in checkpoints:
            if checkpoint.terminal:
                continue
            if checkpoint.next_retry_at and checkpoint.next_retry_at > datetime.now(UTC):
                retry_times.append(checkpoint.next_retry_at)
                continue
            retry_at = await self._execute_source(plan, run_id, checkpoint)
            if retry_at:
                retry_times.append(retry_at)

        latest = await self.collection_repository.checkpoints(plan.id)
        if any(not checkpoint.terminal for checkpoint in latest):
            retry_at = min(retry_times or [datetime.now(UTC) + timedelta(seconds=60)])
            await self.collection_repository.release_for_retry(
                plan_id=plan.id,
                worker_id=self.worker_id,
                retry_at=retry_at,
            )
            return

        exhausted_count = sum(checkpoint.status == 4 for checkpoint in latest)
        await self.ingestion_repository.finish_run(
            run_id,
            failed_count=exhausted_count,
            error_summary=(
                f"{exhausted_count} source(s) exhausted retries" if exhausted_count else None
            ),
            partial=bool(exhausted_count),
        )
        await self.collection_repository.complete_plan(
            plan_id=plan.id,
            worker_id=self.worker_id,
            interval_seconds=plan.interval_seconds,
        )

    async def _execute_source(
        self,
        plan: CollectionPlan,
        run_id: int,
        checkpoint: CollectionCheckpoint,
    ) -> datetime | None:
        offset = checkpoint.offset
        seen_fingerprints: set[str] = set()
        await self.collection_repository.mark_running(plan.id, checkpoint.source)

        while offset < plan.max_results_per_source:
            requested = min(plan.page_size, plan.max_results_per_source - offset)
            try:
                query = self._source_query(plan, checkpoint.source, offset, requested)
                scraped_at = datetime.now(UTC)
                _, result = await asyncio.to_thread(scrape_checked, query)
                if not result.jobs:
                    await self.collection_repository.mark_empty_complete(
                        plan.id,
                        checkpoint.source,
                    )
                    return None

                new_jobs = [
                    job
                    for job in result.jobs
                    if job.source_fingerprint not in seen_fingerprints
                ]
                if not new_jobs:
                    await self.collection_repository.mark_empty_complete(
                        plan.id,
                        checkpoint.source,
                    )
                    return None
                seen_fingerprints.update(job.source_fingerprint for job in new_jobs)

                canonical_jobs = []
                for job in new_jobs:
                    canonical = await asyncio.to_thread(
                        canonical_job_from_jobspy,
                        job,
                        scraped_at=scraped_at,
                        allow_salary_extraction=False,
                    )
                    canonical_jobs.append(canonical)
                await self.ingestion_repository.ingest_batch(
                    run_id=run_id,
                    jobs=canonical_jobs,
                    scraped_at=scraped_at,
                )
                next_offset = offset + len(result.jobs)
                completed = next_offset >= plan.max_results_per_source
                await self.collection_repository.save_page(
                    plan_id=plan.id,
                    source=checkpoint.source,
                    next_offset=next_offset,
                    page_size=len(result.jobs),
                    completed=completed,
                )
                await self.collection_repository.heartbeat(
                    plan_id=plan.id,
                    worker_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                )
                if completed:
                    return None
                offset = next_offset
            except Exception as error:
                attempt = checkpoint.attempts + 1
                exhausted = attempt >= plan.max_attempts
                retry_at = None if exhausted else retry_time(attempt)
                await self.collection_repository.mark_retry(
                    plan_id=plan.id,
                    source=checkpoint.source,
                    attempt=attempt,
                    retry_at=retry_at,
                    exhausted=exhausted,
                    error=f"{type(error).__name__}: {error}",
                )
                return retry_at
        return None

    @staticmethod
    def _source_query(
        plan: CollectionPlan,
        source: str,
        offset: int,
        results_wanted: int,
    ) -> JobSpyQuery:
        values = dict(plan.query)
        source_overrides = values.pop("source_overrides", {})
        values.update(source_overrides.get(source, {}))
        values = resolve_query_location(values, source)
        values.update(
            sites=[source],
            offset=offset,
            results_wanted=results_wanted,
        )
        return JobSpyQuery.model_validate(values)


def retry_time(attempt: int) -> datetime:
    seconds = min(60 * (2 ** (attempt - 1)), 3_600)
    jitter = random.randint(0, 30)
    return datetime.now(UTC) + timedelta(seconds=seconds + jitter)


def scrape_checked(query: JobSpyQuery):
    """Convert JobSpy's logged source failure into a retryable exception.

    Some JobSpy scrapers log an HTTP/parsing error and return an empty DataFrame
    instead of raising. A genuinely empty search has no error log and remains a
    successful terminal page.
    """

    errors: list[str] = []

    class JobSpyErrorCapture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.levelno >= logging.ERROR and record.name.startswith("JobSpy"):
                errors.append(record.getMessage())

    handler = JobSpyErrorCapture()
    root_logger = logging.getLogger()
    jobspy_loggers = [
        logging.getLogger(name)
        for name in logging.root.manager.loggerDict
        if name.startswith("JobSpy:")
    ]
    root_logger.addHandler(handler)
    for logger in jobspy_loggers:
        logger.addHandler(handler)
    try:
        frame, result = scrape_and_normalize(query)
    finally:
        root_logger.removeHandler(handler)
        for logger in jobspy_loggers:
            logger.removeHandler(handler)
    if not result.jobs and errors:
        raise RuntimeError("; ".join(errors[-3:]))
    return frame, result
