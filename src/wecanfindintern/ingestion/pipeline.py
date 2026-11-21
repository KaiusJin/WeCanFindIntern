"""Shared post-collection persistence and enrichment pipeline."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from psycopg_pool import AsyncConnectionPool

from wecanfindintern.db.repositories.jobs import DedupeTimings, JobIngestionRepository
from wecanfindintern.db.repositories.recruiting_term import RecruitingTermRepository
from wecanfindintern.db.repositories.salary import SalaryRepository
from wecanfindintern.deduplication import group_round_jobs
from wecanfindintern.domain.jobs import canonical_job_from_normalized
from wecanfindintern.domain.normalized_job import NormalizedJob
from wecanfindintern.ingestion.recruiting_term_enrichment import (
    RecruitingTermEnrichmentStats,
    enrich_recruiting_terms,
)
from wecanfindintern.ingestion.salary_enrichment import (
    SalaryEnrichmentStats,
    enrich_missing_salaries,
)


@dataclass(frozen=True, slots=True)
class IngestionPipelineReport:
    outcomes: Counter[str]
    salary: SalaryEnrichmentStats
    recruiting_term: RecruitingTermEnrichmentStats
    unique_jobs: int


def _group_batches(groups, limit):
    batch = []
    size = 0
    for group in groups:
        if batch and size + len(group) > limit:
            yield batch
            batch, size = [], 0
        batch.append(group)
        size += len(group)
    if batch:
        yield batch


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
    observed_at: dict[str, datetime] | None = None,
    defer_salary: bool = False,
) -> IngestionPipelineReport:
    """Canonicalize, persist and enrich one already-collected job set."""

    if timings is None:
        timings = DedupeTimings()
    repository = JobIngestionRepository(pool, timings=timings)
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    def prepare_round():
        cards = (
            canonical_job_from_normalized(
                job,
                scraped_at=observed_at.get(job.source_fingerprint, scraped_at)
                if observed_at
                else scraped_at,
            )
            for job in jobs
        )
        return group_round_jobs(cards)

    pipeline_outcomes = outcomes if outcomes is not None else Counter()
    with timings.measure("normalize"):
        groups = await asyncio.to_thread(prepare_round)
    structured = sum(any(job.salary is not None for job in group) for group in groups)
    if groups:
        history_limit = await repository.history_limit()
        for batch in _group_batches(groups, batch_size):
            pipeline_outcomes.update(
                await repository.ingest_groups(
                    run_id=run_id, groups=batch, history_limit=history_limit
                )
            )
    if after_persist is not None:
        after_persist(pipeline_outcomes)

    enriched_salary = (
        SalaryEnrichmentStats()
        if defer_salary
        else await enrich_missing_salaries(SalaryRepository(pool), jobs)
    )
    salary = SalaryEnrichmentStats(
        structured=structured + enriched_salary.structured,
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
        unique_jobs=len(groups),
    )
