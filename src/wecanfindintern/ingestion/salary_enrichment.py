"""Post-deduplication LLM salary enrichment."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass

from wecanfindintern.db.repositories.salary import SalaryRepository
from wecanfindintern.domain.jobs import SalaryRange
from wecanfindintern.domain.normalization import annualize_salary
from wecanfindintern.domain.normalized_job import NormalizedJob
from wecanfindintern.domain.salary import extract_salary_from_description, salary_signal_context
from wecanfindintern.ingestion.salary_llm import extract_salary_with_deepseek_call


@dataclass(frozen=True, slots=True)
class SalaryEnrichmentStats:
    structured: int = 0
    regex: int = 0
    llm: int = 0


async def enrich_missing_salaries(
    repository: SalaryRepository,
    jobs: Iterable[NormalizedJob],
) -> SalaryEnrichmentStats:
    """Run strict post-dedupe passes: source fields, regex, then DeepSeek."""

    fingerprints = [job.source_fingerprint for job in jobs]
    regex_count = 0
    llm_count = 0

    remaining = await repository.salary_enrichment_candidates(fingerprints)
    # Finish regex for the entire deduplicated batch before making any LLM request.
    for candidate in list(remaining):
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
            remaining.remove(candidate)

    signal_candidates = []
    for candidate in remaining:
        if salary_signal_context(candidate.description):
            signal_candidates.append(candidate)
            continue
        await repository.persist_enrichment_check(
            job_id=candidate.job_id,
            description_hash=candidate.description_hash,
            status="not_found",
        )

    # DeepSeek sees only unique jobs that remain salary-less after the regex pass.
    if signal_candidates:
        salary_semaphore = asyncio.Semaphore(5)
        salary_lock = asyncio.Lock()

        async def _enrich_single_salary(candidate) -> None:
            nonlocal llm_count
            async with salary_semaphore:
                call = await asyncio.to_thread(
                    extract_salary_with_deepseek_call,
                    candidate.description,
                    country_code=candidate.country_code,
                    title=candidate.title,
                )
                if call.extraction is not None and await repository.persist_enriched_salary(
                    job_id=candidate.job_id,
                    description_hash=candidate.description_hash,
                    salary=_salary_range(call.extraction),
                    model=call.model,
                ):
                    async with salary_lock:
                        llm_count += 1
                elif call.extraction is None:
                    await repository.persist_enrichment_check(
                        job_id=candidate.job_id,
                        description_hash=candidate.description_hash,
                        status="error" if call.error_type else "not_found",
                        model=call.model,
                    )

        await asyncio.gather(*[_enrich_single_salary(c) for c in signal_candidates])

    return SalaryEnrichmentStats(
        structured=0,
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
