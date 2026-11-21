"""Deterministic salary enrichment over deduplicated jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass

from wecanfindintern.db.repositories.salary import SalaryEnrichmentCandidate, SalaryRepository
from wecanfindintern.domain.jobs import SalaryRange
from wecanfindintern.domain.normalization import annualize_salary
from wecanfindintern.domain.normalized_job import NormalizedJob
from wecanfindintern.domain.salary import (
    ParsedSalary,
    extract_salary_from_description,
    extract_salary_from_label,
)


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
    return await enrich_salaries_for_sources(repository, [job.source_fingerprint for job in jobs])


async def enrich_salaries_for_sources(
    repository: SalaryRepository, fingerprints: Iterable[str]
) -> SalaryEnrichmentStats:
    """Resolve the deduplicated job's current card labels before using its description."""
    label_count = 0
    regex_count = 0
    not_found_count = 0
    failed_count = 0
    for candidate in await repository.salary_enrichment_candidates(fingerprints):
        extracted = await asyncio.to_thread(_candidate_salary, candidate)
        snapshot = {
            "job_id": candidate.job_id,
            "description_hash": candidate.description_hash,
            "input_hash": candidate.input_hash,
            "row_version": candidate.row_version,
        }
        if extracted is None:
            written = await repository.persist_enrichment_check(**snapshot, status="not_found")
            not_found_count += int(written)
        else:
            written = await repository.persist_enriched_salary(
                **snapshot, salary=_salary_range(extracted)
            )
            if extracted.source == "linkedin_salary_label":
                label_count += int(written)
            else:
                regex_count += int(written)
        failed_count += int(not written)
    return SalaryEnrichmentStats(
        structured=label_count,
        regex=regex_count,
        not_found=not_found_count,
        failed=failed_count,
    )


def _candidate_salary(candidate: SalaryEnrichmentCandidate) -> ParsedSalary | None:
    for label in candidate.salary_labels:
        salary = extract_salary_from_label(label, country_code=candidate.country_code)
        if salary is not None:
            return salary
    if candidate.description:
        return extract_salary_from_description(
            candidate.description, country_code=candidate.country_code
        )
    return None


def _salary_range(extracted: ParsedSalary) -> SalaryRange:
    return SalaryRange(
        interval=extracted.interval,
        minimum=extracted.minimum,
        maximum=extracted.maximum,
        currency=extracted.currency,
        source=extracted.source,
        annualized_minimum=annualize_salary(extracted.minimum, extracted.interval),
        annualized_maximum=annualize_salary(extracted.maximum, extracted.interval),
    )
