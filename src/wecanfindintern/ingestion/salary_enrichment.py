"""Post-deduplication LLM salary enrichment."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass

from wecanfindintern.db.ingestion_repository import JobIngestionRepository
from wecanfindintern.domain.jobs import (
    SalaryRange,
    annualize_salary,
    default_salary_currency,
    to_decimal,
)
from wecanfindintern.domain.salary import extract_salary_from_description, validated_salary
from wecanfindintern.domain.salary_llm import extract_salary_with_deepseek
from wecanfindintern.ingestion.jobspy_adapter import NormalizedJob


@dataclass(frozen=True, slots=True)
class SalaryEnrichmentStats:
    structured: int = 0
    regex: int = 0
    llm: int = 0


async def enrich_missing_salaries(
    repository: JobIngestionRepository,
    jobs: Iterable[NormalizedJob],
) -> SalaryEnrichmentStats:
    """Run strict post-dedupe passes: source fields, regex, then DeepSeek."""

    jobs_by_fingerprint = {job.source_fingerprint: job for job in jobs}
    fingerprints = list(jobs_by_fingerprint)
    structured_count = 0
    regex_count = 0
    llm_count = 0

    candidates = await repository.salary_enrichment_candidates(fingerprints)
    for candidate in candidates:
        salary = _structured_salary(
            (
                jobs_by_fingerprint[fingerprint]
                for fingerprint in candidate.source_fingerprints
                if fingerprint in jobs_by_fingerprint
            ),
            country_code=candidate.country_code,
        )
        if salary is not None and await repository.persist_enriched_salary(
            job_id=candidate.job_id,
            description_hash=candidate.description_hash,
            salary=salary,
        ):
            structured_count += 1

    # Finish regex for the entire deduplicated batch before making any LLM request.
    candidates = await repository.salary_enrichment_candidates(fingerprints)
    for candidate in candidates:
        extracted = extract_salary_from_description(
            candidate.description,
            country_code=candidate.country_code,
        )
        if extracted is None:
            continue
        salary = _salary_range(extracted)
        if await repository.persist_enriched_salary(
            job_id=candidate.job_id,
            description_hash=candidate.description_hash,
            salary=salary,
        ):
            regex_count += 1

    # DeepSeek sees only unique jobs that remain salary-less after the regex pass.
    candidates = await repository.salary_enrichment_candidates(fingerprints)
    if candidates:
        salary_semaphore = asyncio.Semaphore(5)
        salary_lock = asyncio.Lock()

        async def _enrich_single_salary(candidate) -> None:
            nonlocal llm_count
            async with salary_semaphore:
                extracted = await asyncio.to_thread(
                    extract_salary_with_deepseek,
                    candidate.description,
                    country_code=candidate.country_code,
                    title=candidate.title,
                )
                if extracted is not None:
                    if await repository.persist_enriched_salary(
                        job_id=candidate.job_id,
                        description_hash=candidate.description_hash,
                        salary=_salary_range(extracted),
                    ):
                        async with salary_lock:
                            llm_count += 1

        await asyncio.gather(*[_enrich_single_salary(c) for c in candidates])

    return SalaryEnrichmentStats(
        structured=structured_count,
        regex=regex_count,
        llm=llm_count,
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


def _structured_salary(
    jobs: Iterable[NormalizedJob],
    *,
    country_code: str | None,
) -> SalaryRange | None:
    for job in jobs:
        source_salary = job.salary
        if (
            source_salary is None
            or not source_salary.interval
            or (source_salary.minimum is None and source_salary.maximum is None)
        ):
            continue
        # JobSpy's description fallback is not provider-structured salary. In
        # annual-enforcement mode it can return annualized amounts while leaving
        # interval="hourly", so always re-parse the original JD ourselves.
        if (source_salary.source or "").casefold() in {
            "description",
            "job_description",
        }:
            continue
        minimum = to_decimal(source_salary.minimum)
        maximum = to_decimal(source_salary.maximum)
        validated = validated_salary(
            interval=source_salary.interval,
            minimum=minimum,
            maximum=maximum,
            currency=(source_salary.currency or default_salary_currency(country_code)).upper(),
            source=source_salary.source or "provider",
        )
        if validated is None:
            continue
        return SalaryRange(
            interval=validated.interval,
            minimum=validated.minimum,
            maximum=validated.maximum,
            currency=validated.currency,
            source=validated.source,
            annualized_minimum=annualize_salary(validated.minimum, validated.interval),
            annualized_maximum=annualize_salary(validated.maximum, validated.interval),
        )
    return None
