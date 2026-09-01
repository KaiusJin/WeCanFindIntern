"""Shared post-collection persistence and enrichment pipeline."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from psycopg_pool import AsyncConnectionPool

from wecanfindintern.db.repositories.jobs import JobIngestionRepository
from wecanfindintern.db.repositories.recruiting_term import RecruitingTermRepository
from wecanfindintern.db.repositories.salary import SalaryRepository
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


async def run_ingestion_pipeline(
    *,
    pool: AsyncConnectionPool,
    run_id: int,
    jobs: list[NormalizedJob],
    scraped_at: datetime,
    batch_size: int,
    outcomes: Counter[str] | None = None,
    after_persist: Callable[[Counter[str]], None] | None = None,
) -> IngestionPipelineReport:
    """Canonicalize, persist and enrich one already-collected job set."""

    repository = JobIngestionRepository(pool)
    canonical_jobs = [
        await asyncio.to_thread(
            canonical_job_from_normalized,
            job,
            scraped_at=scraped_at,
        )
        for job in jobs
    ]
    pipeline_outcomes = outcomes if outcomes is not None else Counter()
    for offset in range(0, len(canonical_jobs), batch_size):
        pipeline_outcomes.update(
            await repository.ingest_batch(
                run_id=run_id,
                jobs=canonical_jobs[offset : offset + batch_size],
                scraped_at=scraped_at,
            )
        )
    if after_persist is not None:
        after_persist(pipeline_outcomes)

    enriched_salary = await enrich_missing_salaries(SalaryRepository(pool), jobs)
    salary = SalaryEnrichmentStats(
        structured=sum(job.salary is not None for job in canonical_jobs),
        regex=enriched_salary.regex,
        llm=enriched_salary.llm,
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
