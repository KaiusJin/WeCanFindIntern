"""Deterministic recruiting-term enrichment over deduplicated jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass

from cloud.storage.recruiting_term import RecruitingTermRepository
from wecanfindintern.domain.recruiting_term import (
    extract_recruiting_term_regex,
    recruiting_term_input_hash,
)


@dataclass(frozen=True, slots=True)
class RecruitingTermEnrichmentStats:
    regex: int = 0
    not_found: int = 0
    skipped_cached: int = 0
    failed: int = 0


async def enrich_recruiting_terms(
    repository: RecruitingTermRepository,
    source_fingerprints: Iterable[str] | None = None,
) -> RecruitingTermEnrichmentStats:
    regex_count = 0
    not_found_count = 0
    cached_count = 0
    failed_count = 0
    for candidate in await repository.recruiting_term_candidates(source_fingerprints):
        input_hash = recruiting_term_input_hash(candidate.title, candidate.description)
        if candidate.checked_input_hash == input_hash:
            cached_count += 1
            continue
        term = await asyncio.to_thread(
            extract_recruiting_term_regex,
            candidate.title,
            candidate.description,
        )
        written = await repository.persist_recruiting_term(
            job_id=candidate.job_id,
            input_hash=input_hash,
            term=term,
        )
        if not written:
            failed_count += 1
        elif term is None:
            not_found_count += 1
        else:
            regex_count += 1
    return RecruitingTermEnrichmentStats(
        regex=regex_count,
        not_found=not_found_count,
        skipped_cached=cached_count,
        failed=failed_count,
    )
