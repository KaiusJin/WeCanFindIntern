"""Indeed/LinkedIn collection stage: run every planned query, then hydrate LinkedIn details."""

from __future__ import annotations

import asyncio
import random
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from cloud.console import log
from cloud.planning.location_query import resolve_query_location
from cloud.sources.jobspy.adapter import (
    JobSpyQuery,
    NormalizedJob,
    fetch_linkedin_details,
    merge_linkedin_details,
    scrape_checked,
)
from cloud.sources.jobspy.shared.exception import LinkedInRateLimitException
from cloud.storage.collection_cache import CollectionCacheRepository
from wecanfindintern.domain.location import parse_location

_RETRYABLE_HTTP_STATUSES = {408, 425, 429}
_PERMANENT_ERROR_MARKERS = ("location not parsed", "has no attribute")


def is_retryable_collection_error(error: Exception) -> bool:
    """Stop deterministic errors and exhausted rate limits for this campaign."""

    if isinstance(error, LinkedInRateLimitException):
        return False
    message = str(error).casefold()
    if any(marker in message for marker in _PERMANENT_ERROR_MARKERS):
        return False
    status_codes = {
        int(value)
        for value in re.findall(
            r"(?:status(?:\s+code)?|http(?:/\d(?:\.\d)?)?)[^0-9]{0,12}(\d{3})",
            message,
        )
    }
    if status_codes:
        return all(
            status in _RETRYABLE_HTTP_STATUSES or 500 <= status < 600 for status in status_codes
        )
    return True


async def collect_queries(
    definitions: list[dict[str, Any]],
    *,
    concurrency: int = 4,
    max_retries: int = 3,
    defer_linkedin_descriptions: bool = False,
    hours_old_override: int | None = None,
    checkpoint=None,
) -> tuple[list[NormalizedJob], list[str], dict[str, int]]:
    collected: dict[str, NormalizedJob] = {}
    failures: list[str] = []
    stats: Counter[str] = Counter(retried=0, succeeded=0, failed=0, skipped=0)
    disabled_sources: set[str] = set()
    lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(max(1, concurrency))
    linkedin_lock = asyncio.Lock()

    tasks: list[tuple[dict[str, Any], str]] = []
    for definition in definitions:
        if not definition.get("enabled", True):
            continue
        for source in definition["sites"]:
            tasks.append((definition, source))

    total_tasks = len(tasks)
    log(f"Starting collection campaign: {total_tasks} site queries with concurrency={concurrency}")

    async def _collect_query(definition: dict[str, Any], source: str, task_idx: int) -> None:
        async with semaphore:
            key = (definition["name"], source)
            cached = checkpoint.completed.get(key) if checkpoint is not None else None
            async with lock:
                source_disabled = cached is None and source in disabled_sources
                if source_disabled:
                    stats["skipped"] += 1
                    stats[f"{source}_skipped"] += 1
            if source_disabled:
                log(
                    f"[{task_idx}/{total_tasks}] Skipped {definition['name']} / {source}: "
                    "provider circuit is open",
                    level="WARN",
                )
                return

            offset = 0
            seen_for_query: set[str] = set()
            maximum = definition.get("max_results_by_source", {}).get(
                source, definition.get("max_results_per_source", 50)
            )
            page_size = definition.get("page_size_by_source", {}).get(
                source, definition.get("page_size", 25)
            )
            query_jobs: list[NormalizedJob] = [] if cached is None else cached
            query_has_failed = False

            while cached is None and offset < maximum:
                requested = min(page_size, maximum - offset)
                values = dict(definition["query"])
                overrides = values.pop("source_overrides", {})
                values.update(overrides.get(source, {}))
                values = resolve_query_location(values, source)
                if hours_old_override is not None and source in {"indeed", "linkedin"}:
                    values["hours_old"] = hours_old_override
                if source == "linkedin" and defer_linkedin_descriptions:
                    values["linkedin_fetch_description"] = False
                query = JobSpyQuery.model_validate(
                    {**values, "sites": [source], "offset": offset, "results_wanted": requested}
                )

                # Automatic retry with exponential backoff and jitter
                retry_count = 0
                result = None
                while True:
                    try:
                        _, result = await asyncio.to_thread(scrape_checked, query)
                        break
                    except Exception as error:
                        retryable = is_retryable_collection_error(error)
                        if not retryable:
                            query_has_failed = True
                            async with lock:
                                disabled_sources.add(source)
                            log(
                                f"[{task_idx}/{total_tasks}] FAILED; "
                                f"opened circuit for {source}: "
                                f"{definition['name']} / {source} "
                                f"({type(error).__name__}: {error})",
                                level="ERROR",
                            )
                            async with lock:
                                failures.append(
                                    f"{definition['name']}:{source}:{type(error).__name__}: {error}"
                                )
                            break
                        if retry_count >= max_retries:
                            query_has_failed = True
                            log(
                                f"[{task_idx}/{total_tasks}] FAILED after {max_retries} retries: "
                                f"{definition['name']} / {source} "
                                f"({type(error).__name__}: {error})",
                                level="ERROR",
                            )
                            async with lock:
                                failures.append(
                                    f"{definition['name']}:{source}:{type(error).__name__}: {error}"
                                )
                            break
                        retry_count += 1
                        async with lock:
                            stats["retried"] += 1
                        backoff = min(
                            15.0,
                            (2 ** (retry_count - 1)) * 1.5 + random.uniform(0.5, 2.0),
                        )
                        log(
                            f"[{task_idx}/{total_tasks}] Retry {retry_count}/{max_retries} "
                            f"in {backoff:.1f}s for {definition['name']} / {source} "
                            f"({type(error).__name__}: {error})",
                            level="WARN",
                        )
                        await asyncio.sleep(backoff)

                if query_has_failed or result is None or not result.jobs:
                    break

                new_jobs = [
                    job
                    for job in result.jobs
                    if job.source_fingerprint not in seen_for_query
                    and _is_in_country_scope(job, values.get("country_indeed"))
                ]
                if not new_jobs:
                    break
                seen_for_query.update(job.source_fingerprint for job in new_jobs)
                query_jobs.extend(new_jobs)
                offset += len(result.jobs)

            if checkpoint is not None and cached is None and not query_has_failed:
                await asyncio.to_thread(checkpoint.save, key, query_jobs)

            async with lock:
                if query_has_failed:
                    stats["failed"] += 1
                    stats[f"{source}_failed"] += 1
                else:
                    stats["succeeded"] += 1
                    stats[f"{source}_succeeded"] += 1
                for job in query_jobs:
                    existing = collected.get(job.source_fingerprint)
                    if existing is None or _record_score(job) > _record_score(existing):
                        collected[job.source_fingerprint] = job

            if not query_has_failed:
                log(
                    f"[{task_idx}/{total_tasks}] Collected {definition['name']} / {source}: "
                    f"{len(seen_for_query)} source jobs (unique total: {len(collected)})"
                )

    async def _collect_single_query(definition: dict[str, Any], source: str, task_idx: int) -> None:
        if source == "linkedin":
            # Wait outside the global semaphore so Indeed can keep collecting
            # while LinkedIn is throttled. Recheck the source circuit inside.
            async with linkedin_lock:
                await _collect_query(definition, source, task_idx)
        else:
            await _collect_query(definition, source, task_idx)

    await asyncio.gather(
        *[_collect_single_query(defn, src, idx) for idx, (defn, src) in enumerate(tasks, start=1)]
    )
    return list(collected.values()), failures, stats


def linkedin_details_enabled(definitions: list[dict[str, Any]]) -> bool:
    for definition in definitions:
        if not definition.get("enabled", True) or "linkedin" not in definition["sites"]:
            continue
        values = dict(definition["query"])
        overrides = values.pop("source_overrides", {})
        values.update(overrides.get("linkedin", {}))
        if values.get("linkedin_fetch_description", False):
            return True
    return False


async def hydrate_linkedin_descriptions(
    jobs: list[NormalizedJob],
    cache: CollectionCacheRepository,
    *,
    ttl_seconds: int,
    concurrency: int,
) -> tuple[list[NormalizedJob], dict[str, int]]:
    """Reuse fresh LinkedIn JDs and fetch each remaining source ID only once."""

    linkedin_jobs = [job for job in jobs if job.source == "linkedin"]
    cached = await cache.linkedin_details([job.source_fingerprint for job in linkedin_jobs])
    fresh_since = datetime.now(UTC) - timedelta(seconds=max(0, ttl_seconds))
    hydrated: dict[str, NormalizedJob] = {}
    cache_hits = 0
    for job in linkedin_jobs:
        entry = cached.get(job.source_fingerprint)
        if (
            entry is None
            or entry.details_fetched_at < fresh_since
            or not _linkedin_card_matches_cache(job, entry.payload)
        ):
            continue
        hydrated[job.source_fingerprint] = merge_linkedin_details(
            job,
            entry.payload,
            fetched_at=entry.details_fetched_at,
        )
        cache_hits += 1

    semaphore = asyncio.Semaphore(max(1, concurrency))
    fetched = 0
    failed = 0
    stale_fallbacks = 0
    counter_lock = asyncio.Lock()
    rate_limited = False

    async def _fetch(job: NormalizedJob) -> None:
        nonlocal fetched, failed, stale_fallbacks, rate_limited
        if job.source_fingerprint in hydrated:
            return
        if not job.source_job_id:
            async with counter_lock:
                failed += 1
            return
        async with semaphore:
            try:
                details = (
                    {}
                    if rate_limited
                    else await asyncio.to_thread(fetch_linkedin_details, job.source_job_id)
                )
            except LinkedInRateLimitException as error:
                rate_limited = True
                log(f"Paused LinkedIn detail fetches: {error}", level="WARN")
                details = {}
            except Exception as error:
                log(
                    f"LinkedIn detail fetch failed for {job.source_job_id}: "
                    f"{type(error).__name__}: {error}",
                    level="WARN",
                )
                details = {}
        if details.get("description"):
            hydrated[job.source_fingerprint] = merge_linkedin_details(job, details)
            async with counter_lock:
                fetched += 1
        else:
            stale = cached.get(job.source_fingerprint)
            if stale is not None:
                hydrated[job.source_fingerprint] = merge_linkedin_details(
                    job,
                    stale.payload,
                    fetched_at=stale.details_fetched_at,
                )
                async with counter_lock:
                    stale_fallbacks += 1
            async with counter_lock:
                failed += 1

    await asyncio.gather(*[_fetch(job) for job in linkedin_jobs])
    merged_jobs = [hydrated.get(job.source_fingerprint, job) for job in jobs]
    return merged_jobs, {
        "linkedin_detail_cache_hits": cache_hits,
        "linkedin_detail_fetched": fetched,
        "linkedin_detail_failed": failed,
        "linkedin_detail_stale_fallbacks": stale_fallbacks,
    }


def _linkedin_card_matches_cache(job: NormalizedJob, payload: dict[str, Any]) -> bool:
    for field in ("title", "company", "location"):
        current = job.raw.get(field)
        previous = payload.get(field)
        if current not in (None, "") and previous not in (None, "") and current != previous:
            return False
    return True


def _record_score(job: NormalizedJob) -> tuple[int, int]:
    has_salary = int(
        bool(job.salary and (job.salary.minimum is not None or job.salary.maximum is not None))
    )
    return has_salary, len(job.description or "")


def _is_in_country_scope(job: NormalizedJob, requested_country: str | None) -> bool:
    requested_code = {"canada": "CA", "usa": "US", "united states": "US"}.get(
        (requested_country or "").casefold()
    )
    if requested_code is None:
        return True
    parsed = parse_location(job.location_text)
    return parsed.country_code == requested_code
