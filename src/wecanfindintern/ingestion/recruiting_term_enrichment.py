"""Post-deduplication recruiting term enrichment: regex first, then DeepSeek."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterable
from dataclasses import dataclass

from wecanfindintern.db.repositories.recruiting_term import RecruitingTermRepository
from wecanfindintern.domain.recruiting_term import (
    extract_recruiting_term_regex,
    recruiting_term_input_hash,
    recruiting_term_signal_context,
)
from wecanfindintern.domain.recruiting_term_llm import (
    extract_recruiting_term_with_deepseek,
    extract_recruiting_terms_batch,
)


@dataclass(frozen=True, slots=True)
class RecruitingTermEnrichmentStats:
    regex: int = 0
    llm: int = 0
    not_found: int = 0
    skipped_cached: int = 0
    failed: int = 0
    llm_deferred: int = 0


async def enrich_recruiting_terms(
    repository: RecruitingTermRepository,
    source_fingerprints: Iterable[str] | None = None,
    *,
    allow_llm: bool = True,
) -> RecruitingTermEnrichmentStats:
    candidates = await repository.recruiting_term_candidates(source_fingerprints)
    unresolved = []
    regex_count = 0
    not_found_count = 0
    cached_count = 0
    llm_count = 0
    failed_count = 0

    # Complete regex over every unique job before the first DeepSeek request.
    for candidate in candidates:
        input_hash = recruiting_term_input_hash(candidate.title, candidate.description)
        if candidate.checked_input_hash == input_hash:
            cached_count += 1
            continue
        term = extract_recruiting_term_regex(candidate.title, candidate.description)
        if term is not None:
            if await repository.persist_recruiting_term(
                job_id=candidate.job_id,
                input_hash=input_hash,
                term=term,
            ):
                regex_count += 1
            continue
        context = recruiting_term_signal_context(candidate.title, candidate.description)
        if context is None:
            if await repository.persist_recruiting_term(
                job_id=candidate.job_id,
                input_hash=input_hash,
                term=None,
            ):
                not_found_count += 1
            continue
        unresolved.append((candidate, input_hash, context))

    # DeepSeek sees only signal-bearing jobs unresolved by the global regex pass.
    if not allow_llm:
        return RecruitingTermEnrichmentStats(
            regex=regex_count,
            not_found=not_found_count,
            skipped_cached=cached_count,
            llm_deferred=len(unresolved),
        )

    model = os.getenv("DEEPSEEK_TERM_MODEL", "deepseek-chat")
    total_unresolved = len(unresolved)
    batch_size = int(os.getenv("DEEPSEEK_TERM_BATCH_SIZE", "8"))

    async def _record(candidate, input_hash: str, context: str, call, generation_id) -> None:
        nonlocal llm_count, not_found_count, failed_count
        await repository.finish_recruiting_term_generation(
            generation_id,
            response_json=call.response_json,
            prompt_tokens=call.prompt_tokens,
            completion_tokens=call.completion_tokens,
            error_type=call.error_type,
        )
        if call.error_type is not None:
            failed_count += 1
            return
        if await repository.persist_recruiting_term(
            job_id=candidate.job_id,
            input_hash=input_hash,
            term=call.extraction,
            model=call.model,
        ):
            if call.extraction is None:
                not_found_count += 1
            else:
                llm_count += 1

    if batch_size > 0:
        # One DeepSeek call per group of contexts; per-job generation records
        # are created up front and completed after the batch call returns.
        for batch_start in range(0, len(unresolved), batch_size):
            batch = unresolved[batch_start : batch_start + batch_size]
            generation_ids = [
                await repository.start_recruiting_term_generation(
                    job_id=candidate.job_id,
                    input_hash=input_hash,
                    input_context=context,
                    model=model,
                )
                for candidate, input_hash, context in batch
            ]
            calls = await asyncio.to_thread(
                extract_recruiting_terms_batch, [context for _, _, context in batch]
            )
            for (candidate, input_hash, context), call, generation_id in zip(
                batch, calls, generation_ids, strict=True
            ):
                await _record(candidate, input_hash, context, call, generation_id)
            print(
                f"recruiting term DeepSeek batch progress: "
                f"{min(batch_start + batch_size, total_unresolved)}/{total_unresolved}, "
                f"matched={llm_count}, failed={failed_count}",
                flush=True,
            )
    else:
        semaphore = asyncio.Semaphore(5)
        lock = asyncio.Lock()
        processed_count = 0

        async def _process_candidate(candidate, input_hash: str, context: str) -> None:
            nonlocal llm_count, not_found_count, failed_count, processed_count
            async with semaphore:
                generation_id = await repository.start_recruiting_term_generation(
                    job_id=candidate.job_id,
                    input_hash=input_hash,
                    input_context=context,
                    model=model,
                )
                call = await asyncio.to_thread(
                    extract_recruiting_term_with_deepseek, context
                )
                async with lock:
                    await _record(candidate, input_hash, context, call, generation_id)
                    processed_count += 1
                    if processed_count % 10 == 0 or processed_count == total_unresolved:
                        print(
                            f"recruiting term DeepSeek progress: "
                            f"{processed_count}/{total_unresolved}, "
                            f"matched={llm_count}, failed={failed_count}",
                            flush=True,
                        )

        await asyncio.gather(*[_process_candidate(c, h, ctx) for c, h, ctx in unresolved])

    return RecruitingTermEnrichmentStats(
        regex=regex_count,
        llm=llm_count,
        not_found=not_found_count,
        skipped_cached=cached_count,
        failed=failed_count,
    )
