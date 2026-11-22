#!/usr/bin/env python3
"""Collect every configured query first, then deduplicate and normalize with regex."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import random
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jobspy.exception import LinkedInRateLimitException

from wecanfindintern.config import Settings
from wecanfindintern.db.pool import Database
from wecanfindintern.db.repositories.collection_cache import CollectionCacheRepository
from wecanfindintern.db.repositories.jobs import DedupeTimings, JobIngestionRepository
from wecanfindintern.domain.location import parse_location
from wecanfindintern.ingestion.ats import collect_ats_jobs, load_boards
from wecanfindintern.ingestion.ats.boards import merge_boards
from wecanfindintern.ingestion.ats.providers import Scope as AtsScope
from wecanfindintern.ingestion.ats.simplify import (
    SimplifyListing,
    coverage_report,
    fetch_simplify,
    gap_fill_jobs,
)
from wecanfindintern.ingestion.ats.stapply import collect_stapply_jobs
from wecanfindintern.ingestion.collection_catalog import expand_collection_catalog
from wecanfindintern.ingestion.jobspy_adapter import (
    JobSpyQuery,
    NormalizedJob,
    fetch_linkedin_details,
    merge_linkedin_details,
    scrape_checked,
)
from wecanfindintern.ingestion.location_query import resolve_query_location
from wecanfindintern.ingestion.pipeline import run_ingestion_pipeline

_RETRYABLE_HTTP_STATUSES = {408, 425, 429}
_PERMANENT_ERROR_MARKERS = ("location not parsed", "has no attribute")


@dataclass(frozen=True, slots=True)
class CampaignResult:
    """Structured collection result used by the cloud publisher."""

    completed_at: datetime
    duration_seconds: float
    status: str
    unique_jobs_collected: int
    database_stats: dict[str, int]
    salary_stats: dict[str, int]
    recruiting_term_stats: dict[str, int]
    query_stats: dict[str, int]
    failures: tuple[str, ...]
    dedupe_seconds: dict[str, float] = dataclasses.field(default_factory=dict)
    # Operator report: per-source counts, timings and SimplifyJobs coverage.
    monitor: dict[str, Any] = dataclasses.field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {
            "completed_at": self.completed_at.isoformat(),
            "duration_seconds": round(self.duration_seconds, 2),
            "status": self.status,
            "unique_jobs_collected": self.unique_jobs_collected,
            "database_stats": dict(self.database_stats),
            "dedupe_seconds": self.dedupe_seconds,
            "salary_stats": dict(self.salary_stats),
            "recruiting_term_stats": dict(self.recruiting_term_stats),
            "query_stats": dict(self.query_stats),
            "monitor": self.monitor,
            "failure_count": len(self.failures),
            "failures": list(self.failures),
        }


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


def log(message: str, level: str = "INFO") -> None:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] [{level}] {message}", flush=True)


async def collect_all(
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


def source_group(job: NormalizedJob) -> str:
    """Monitoring bucket: one per collector, not per underlying ATS."""
    via = (job.raw or {}).get("via")
    if via in {"stapply", "simplify"}:
        return via
    if job.source in {"greenhouse", "lever", "ashby", "workday"}:
        return f"ats_{job.source}"
    return job.source


def build_source_monitor(
    jobs: list[NormalizedJob], listings: list[SimplifyListing]
) -> dict[str, Any]:
    """Per-collector job counts and SimplifyJobs recall, before gap filling."""
    groups: dict[str, list[NormalizedJob]] = {}
    for job in jobs:
        groups.setdefault(source_group(job), []).append(job)
    monitor: dict[str, Any] = {
        "sources": {name: {"jobs": len(items)} for name, items in sorted(groups.items())}
    }
    if listings:
        monitor["coverage"] = coverage_report(listings, jobs)
        for name, items in groups.items():
            report = coverage_report(listings, items, sample=0)
            monitor["sources"][name]["simplify_covered"] = report["covered"]
            monitor["sources"][name]["simplify_recall"] = report["recall"]
    return monitor


def ats_settings(defaults: dict[str, Any]) -> dict[str, Any]:
    settings = {
        "enabled": True,
        "boards_path": "ats_boards.json",
        "concurrency": 8,
        "include_new_grad": False,
        "budget_seconds": 900,
        "stapply_enabled": True,
        "simplify_enabled": True,
    }
    settings.update(defaults.get("ats") or {})
    env = os.getenv("WCFI_ATS_ENABLED")
    if env is not None:
        settings["enabled"] = env.strip().lower() not in {"0", "false", "no", "off"}
    env = os.getenv("WCFI_SIMPLIFY_ENABLED")
    if env is not None:
        settings["simplify_enabled"] = env.strip().lower() not in {"0", "false", "no", "off"}
    env = os.getenv("WCFI_STAPPLY_ENABLED")
    if env is not None:
        settings["stapply_enabled"] = env.strip().lower() not in {"0", "false", "no", "off"}
    return settings


async def collect_ats_stage(
    config_path: Path, defaults: dict[str, Any]
) -> tuple[list[NormalizedJob], dict[str, int], list[SimplifyListing]]:
    """Backfill from the Stapply dataset, then poll Greenhouse/Lever/Ashby/Workday boards.

    A failed board, or an unavailable Stapply snapshot, is logged and counted but
    never fails the campaign: it is retried next run, and jobs already collected
    stay live until the normal 14-day expiry.
    """
    settings = ats_settings(defaults)
    if not settings["enabled"]:
        log("ATS stage disabled")
        return [], {}, []
    boards_path = config_path.parent / settings["boards_path"]
    boards = load_boards(boards_path)
    scope = AtsScope(include_new_grad=bool(settings["include_new_grad"]))
    stapply_jobs: list[NormalizedJob] = []
    stats: dict[str, int] = {}
    simplify_listings: list[SimplifyListing] = []
    if settings["simplify_enabled"]:
        simplify_started = time.monotonic()
        try:
            simplify = await asyncio.to_thread(fetch_simplify, scope)
        except Exception as error:  # third-party list: never fail the campaign
            log(f"SimplifyJobs lists skipped: {type(error).__name__}: {error}", "WARN")
            stats["simplify_failed"] = 1
        else:
            simplify_listings = simplify.listings
            stats.update({f"simplify_{k}": int(v) for k, v in simplify.stats.items()})
            known = len(boards)
            boards = merge_boards(boards, simplify.boards)
            stats["ats_boards_from_simplify"] = len(boards) - known
            stats["simplify_seconds"] = round(time.monotonic() - simplify_started)
            log(
                f"SimplifyJobs lists loaded: {len(simplify_listings)} in-scope listings, "
                f"{len(boards) - known} new boards to poll"
            )
    if settings["stapply_enabled"]:
        # Stapply backfills every ATS/career site this collector does not poll, and
        # its Greenhouse/Lever/Ashby/Workday URLs reveal boards missing from the registry.
        stapply_started = time.monotonic()
        try:
            stapply = await asyncio.to_thread(collect_stapply_jobs, scope)
        except Exception as error:  # third-party dataset: never fail the campaign
            log(f"Stapply backfill skipped: {type(error).__name__}: {error}", "WARN")
            stats["stapply_failed"] = 1
        else:
            stapply_jobs = stapply.jobs
            stats.update({f"stapply_{k}": int(v) for k, v in stapply.stats.items()})
            known = len(boards)
            boards = merge_boards(boards, stapply.boards)
            stats["ats_boards_from_stapply"] = len(boards) - known
            stats["stapply_seconds"] = round(time.monotonic() - stapply_started)
            log(
                f"Stapply backfill complete in {time.monotonic() - stapply_started:.1f}s: "
                f"{len(stapply_jobs)} jobs, {len(boards) - known} new boards to poll"
            )
    started = time.monotonic()
    collection = await asyncio.to_thread(
        collect_ats_jobs,
        boards,
        scope=scope,
        concurrency=int(settings["concurrency"]),
        deadline=started + float(settings["budget_seconds"]),
    )
    stats.update({f"ats_{key}": int(value) for key, value in collection.stats.items()})
    stats["ats_boards"] = len(boards)
    stats["ats_seconds"] = round(time.monotonic() - started)
    log(
        f"ATS stage complete in {time.monotonic() - started:.1f}s: "
        f"{len(collection.jobs)} jobs from {stats.get('ats_boards_ok', 0)}/{len(boards)} boards "
        f"(missing={stats.get('ats_boards_missing', 0)}, "
        f"failed={stats.get('ats_boards_failed', 0)}, "
        f"skipped_deadline={stats.get('ats_boards_skipped_deadline', 0)})"
    )
    for failure in collection.failed_boards[:20]:
        log(f"ATS board failed: {failure}", "WARN")
    return [*collection.jobs, *stapply_jobs], stats, simplify_listings


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


def collection_hours_window(
    completed_campaigns: int,
    *,
    recent_hours: int,
    full_sweep_every: int,
) -> int | None:
    """Return None for full sweeps, otherwise the rolling provider age window."""

    if completed_campaigns < 0:
        raise ValueError("completed_campaigns cannot be negative")
    if recent_hours < 1:
        raise ValueError("recent_hours must be positive")
    if full_sweep_every < 1:
        raise ValueError("full_sweep_every must be positive")
    return None if completed_campaigns % full_sweep_every == 0 else recent_hours


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


async def run(
    config_path: Path,
    batch_size: int,
    concurrency: int = 4,
    max_retries: int = 3,
    *,
    checkpoint=None,
) -> CampaignResult:
    start_time = time.monotonic()
    raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    definitions = expand_collection_catalog(raw_config)
    defaults = raw_config.get("defaults", {}) if isinstance(raw_config, dict) else {}
    recent_hours = int(os.getenv("WCFI_COLLECTION_RECENT_HOURS", defaults.get("recent_hours", 48)))
    full_sweep_every = int(
        os.getenv(
            "WCFI_COLLECTION_FULL_SWEEP_EVERY",
            defaults.get("full_sweep_every", 10),
        )
    )

    database = Database(Settings.from_env())
    await database.open()
    repository: JobIngestionRepository | None = None
    run_record = None
    counts: Counter[str] = Counter()
    failures: list[str] = []
    query_stats: dict[str, int] = {}
    result: CampaignResult | None = None
    try:
        collection_cache = CollectionCacheRepository(database.pool)
        completed_campaigns = await collection_cache.completed_campaign_count(config_path.name)
        hours_old = collection_hours_window(
            completed_campaigns,
            recent_hours=recent_hours,
            full_sweep_every=full_sweep_every,
        )
        sweep_number = completed_campaigns + 1
        sweep_mode = "full" if hours_old is None else f"recent-{hours_old}h"
        log(
            f"Collection sweep #{sweep_number}: mode={sweep_mode}, "
            f"full sweep every {full_sweep_every} completed campaigns"
        )

        # Stage 1a: collect provider list pages first. LinkedIn details are deferred
        # until IDs have been deduplicated across every keyword query.
        collection_started = time.monotonic()
        defer_linkedin = linkedin_details_enabled(definitions)
        if checkpoint is not None:
            checkpoint.restore(sweep_number)
        normalized_jobs, failures, query_stats = await collect_all(
            definitions,
            concurrency=concurrency,
            max_retries=max_retries,
            defer_linkedin_descriptions=defer_linkedin,
            hours_old_override=hours_old,
            checkpoint=checkpoint,
        )
        query_stats.update(
            {
                "full_sweep": int(hours_old is None),
                "recent_hours": hours_old or 0,
                "sweep_number": sweep_number,
            }
        )

        if defer_linkedin:
            normalized_jobs, detail_stats = await hydrate_linkedin_descriptions(
                normalized_jobs,
                collection_cache,
                ttl_seconds=int(
                    os.getenv(
                        "WCFI_LINKEDIN_DETAIL_TTL_SECONDS",
                        defaults.get("linkedin_detail_ttl_seconds", 86_400),
                    )
                ),
                concurrency=int(
                    os.getenv(
                        "WCFI_LINKEDIN_DETAIL_CONCURRENCY",
                        defaults.get("linkedin_detail_concurrency", 4),
                    )
                ),
            )
            query_stats.update(detail_stats)
            log(
                "LinkedIn detail stage complete: "
                f"cached={detail_stats['linkedin_detail_cache_hits']}, "
                f"fetched={detail_stats['linkedin_detail_fetched']}, "
                f"failed={detail_stats['linkedin_detail_failed']}, "
                f"stale_fallbacks={detail_stats['linkedin_detail_stale_fallbacks']}"
            )
        ats_jobs, ats_stats, simplify_listings = await collect_ats_stage(config_path, defaults)
        normalized_jobs = [*normalized_jobs, *ats_jobs]
        query_stats.update(ats_stats)
        monitor = build_source_monitor(normalized_jobs, simplify_listings)
        if simplify_listings:
            fill = gap_fill_jobs(simplify_listings, normalized_jobs)
            normalized_jobs = [*normalized_jobs, *fill]
            query_stats["simplify_gap_fill_jobs"] = len(fill)
            monitor["simplify_gap_fill_jobs"] = len(fill)
            log(
                "SimplifyJobs coverage before gap fill: "
                f"{monitor['coverage']['covered']}/{monitor['coverage']['reference']}; "
                f"added {len(fill)} gap-fill jobs"
            )

        collection_seconds = time.monotonic() - collection_started
        log(
            f"Collection stage complete in {collection_seconds:.1f}s: "
            f"{len(normalized_jobs)} unique source records "
            f"(queries succeeded={query_stats['succeeded']}, "
            f"failed={query_stats['failed']}, retried={query_stats['retried']})"
        )

        repository = JobIngestionRepository(database.pool)
        sources = sorted({job.source for job in normalized_jobs})
        run_record = await repository.start_run(
            sources=sources,
            query={
                "campaign": config_path.name,
                "plan_count": len(definitions),
                "sweep_number": sweep_number,
                "sweep_mode": sweep_mode,
                "recent_hours": hours_old,
            },
        )
        scraped_at = datetime.now(UTC)
        timings = DedupeTimings()
        dedupe_seconds: dict[str, float] = {}

        def log_persistence(persisted: Counter[str]) -> None:
            elapsed = time.monotonic() - persistence_started
            dedupe_seconds.update(timings.summary(elapsed))
            log(
                f"Dedupe stage complete in {elapsed:.1f}s: created={persisted['created']}, "
                f"merged={persisted['merged']}, updated={persisted['updated']}, "
                f"unchanged={persisted['unchanged']}"
            )
            log(f"Dedupe timing (seconds): {json.dumps(dedupe_seconds)}")

        persistence_started = time.monotonic()
        pipeline = await run_ingestion_pipeline(
            pool=database.pool,
            run_id=run_record.internal_id,
            jobs=normalized_jobs,
            scraped_at=scraped_at,
            batch_size=batch_size,
            outcomes=counts,
            timings=timings,
            after_persist=log_persistence,
        )

        # Stages 3 and 4: deterministic post-deduplication enrichment
        salary = pipeline.salary
        log(
            f"Salary stages complete: source={salary.structured}, "
            f"regex={salary.regex}, not_found={salary.not_found}, failed={salary.failed}"
        )

        term = pipeline.recruiting_term
        log(
            f"Recruiting term stages complete: regex={term.regex}, "
            f"not_found={term.not_found}, "
            f"cached={term.skipped_cached}, failed={term.failed}"
        )

        await repository.finish_run(
            run_record.internal_id,
            failed_count=len(failures),
            error_summary="\n".join(failures)[:2000] or None,
            partial=bool(failures),
        )
        duration = time.monotonic() - start_time
        result_status = (
            "failed" if failures and not normalized_jobs else "partial" if failures else "success"
        )
        result = CampaignResult(
            completed_at=datetime.now(UTC),
            duration_seconds=duration,
            status=result_status,
            unique_jobs_collected=len(normalized_jobs),
            dedupe_seconds=dedupe_seconds,
            database_stats={
                "created": counts["created"],
                "merged": counts["merged"],
                "updated": counts["updated"],
                "unchanged": counts["unchanged"],
            },
            salary_stats={
                "structured": salary.structured,
                "regex": salary.regex,
                "not_found": salary.not_found,
                "failed": salary.failed,
            },
            recruiting_term_stats={
                "regex": term.regex,
                "not_found": term.not_found,
                "cached": term.skipped_cached,
                "failed": term.failed,
            },
            query_stats=query_stats,
            failures=tuple(failures),
            monitor={
                **monitor,
                "sources": {
                    **monitor.get("sources", {}),
                    **{
                        source: {**monitor.get("sources", {}).get(source, {}), "final_jobs": n}
                        for source, n in Counter(job.source for job in normalized_jobs).items()
                    },
                },
            },
        )
        summary_path = Path(os.getenv("WCFI_LOG_DIR", "logs")) / "campaign_summary_latest.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(result.payload(), indent=2), encoding="utf-8")
        log(f"Collection campaign finished in {duration:.1f}s. Summary written to {summary_path}")

    except Exception as error:
        if repository is not None and run_record is not None:
            await repository.finish_run(
                run_record.internal_id,
                failed_count=max(1, len(failures)),
                error_summary=str(error)[:2000],
                partial=bool(counts),
            )
        log(f"Campaign encountered fatal error: {error}", level="ERROR")
        raise
    finally:
        await database.close()

    if failures:
        log(f"Source failures encountered: {len(failures)}", level="WARN")
        for failure in failures:
            log(f"- {failure}", level="WARN")
    if result is None:
        raise RuntimeError("collection campaign ended without a result")
    return result
