"""Shared post-collection persistence and enrichment pipeline."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter

from psycopg_pool import AsyncConnectionPool

from cloud.console import log
from cloud.pipeline.recruiting_term import (
    RecruitingTermEnrichmentStats,
    enrich_recruiting_terms,
)
from cloud.pipeline.salary import (
    SalaryEnrichmentStats,
    enrich_missing_salaries,
)
from cloud.storage.jobs import DedupeTimings, JobIngestionRepository
from cloud.storage.recruiting_term import RecruitingTermRepository
from cloud.storage.salary import SalaryRepository
from wecanfindintern.domain.jobs import canonical_job_from_normalized
from wecanfindintern.domain.normalized_job import NormalizedJob


@dataclass(frozen=True, slots=True)
class IngestionPipelineReport:
    outcomes: Counter[str]
    salary: SalaryEnrichmentStats
    recruiting_term: RecruitingTermEnrichmentStats


async def run_ingestion_pipeline(
    *,
    pool: AsyncConnectionPool,
    run_id: int,
    jobs: list[NormalizedJob],
    scraped_at: datetime,
    batch_size: int,
    outcomes: Counter[str] | None = None,
    timings: DedupeTimings | None = None,
    after_persist: Callable[[Counter[str]], None] | None = None,
) -> IngestionPipelineReport:
    """Canonicalize, persist and enrich one already-collected job set."""

    if timings is None:
        timings = DedupeTimings()
    repository = JobIngestionRepository(pool, timings=timings)
    started = perf_counter()
    total = len(jobs)
    batches = (total + batch_size - 1) // batch_size
    log(f"Ingestion started: {total} records, {batches} batches of up to {batch_size}")
    canonical_jobs = []
    pipeline_outcomes = outcomes if outcomes is not None else Counter()
    for offset in range(0, total, batch_size):
        batch_started = perf_counter()
        with timings.measure("normalize"):
            batch = [
                await asyncio.to_thread(canonical_job_from_normalized, job, scraped_at=scraped_at)
                for job in jobs[offset : offset + batch_size]
            ]
        canonical_jobs.extend(batch)
        persisted = await repository.ingest_batch(run_id=run_id, jobs=batch, scraped_at=scraped_at)
        pipeline_outcomes.update(persisted)
        processed = offset + len(batch)
        elapsed = perf_counter() - started
        log(
            f"Ingestion batch {offset // batch_size + 1}/{batches}: "
            f"processed={processed}/{total} ({100 * processed / total:.1f}%), "
            f"created={persisted['created']}, merged={persisted['merged']}, "
            f"updated={persisted['updated']}, unchanged={persisted['unchanged']}; "
            f"batch={perf_counter() - batch_started:.1f}s, elapsed={elapsed:.1f}s"
        )
    if after_persist is not None:
        after_persist(pipeline_outcomes)

    enriched_salary = await enrich_missing_salaries(SalaryRepository(pool), jobs)
    salary = SalaryEnrichmentStats(
        structured=sum(job.salary is not None for job in canonical_jobs),
        regex=enriched_salary.regex,
        not_found=enriched_salary.not_found,
        failed=enriched_salary.failed,
    )
    recruiting_term = await enrich_recruiting_terms(
        RecruitingTermRepository(pool),
        [job.source_fingerprint for job in jobs],
    )
    return IngestionPipelineReport(
        outcomes=pipeline_outcomes,
        salary=salary,
        recruiting_term=recruiting_term,
    )
