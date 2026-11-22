"""Deterministic salary enrichment over deduplicated jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass

from cloud.storage.salary import SalaryRepository
from wecanfindintern.domain.jobs import SalaryRange
from wecanfindintern.domain.normalization import annualize_salary
from wecanfindintern.domain.normalized_job import NormalizedJob
from wecanfindintern.domain.salary import extract_salary_from_description


@dataclass(frozen=True, slots=True)
class SalaryEnrichmentStats:
    structured: int = 0
    regex: int = 0
    not_found: int = 0
    failed: int = 0


async def enrich_missing_salaries(
    repository: SalaryRepository,
    jobs: Iterable[NormalizedJob],
) -> SalaryEnrichmentStats:
    """Keep provider salary; fill missing salaries with regex only after deduplication."""
    fingerprints = [job.source_fingerprint for job in jobs]
    regex_count = 0
    not_found_count = 0
    failed_count = 0
    for candidate in await repository.salary_enrichment_candidates(fingerprints):
        extracted = await asyncio.to_thread(
            extract_salary_from_description,
            candidate.description,
            country_code=candidate.country_code,
        )
        if extracted is None:
            written = await repository.persist_enrichment_check(
                job_id=candidate.job_id,
                description_hash=candidate.description_hash,
                status="not_found",
            )
            not_found_count += int(written)
        else:
            written = await repository.persist_enriched_salary(
                job_id=candidate.job_id,
                description_hash=candidate.description_hash,
                salary=_salary_range(extracted),
            )
            regex_count += int(written)
        failed_count += int(not written)
    return SalaryEnrichmentStats(
        regex=regex_count,
        not_found=not_found_count,
        failed=failed_count,
    )


def _salary_range(extracted) -> SalaryRange:
    return SalaryRange(
        interval=extracted.interval,
        minimum=extracted.minimum,
        maximum=extracted.maximum,
        currency=extracted.currency,
        source=extracted.source,
        annualized_minimum=annualize_salary(extracted.minimum, extracted.interval),
        annualized_maximum=annualize_salary(extracted.maximum, extracted.interval),
    )
