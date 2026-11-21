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
from collections import Counter, deque
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import psycopg
from jobspy.exception import (
    InvalidPageCursor,
    LinkedInJobGone,
    LinkedInRateLimitException,
    PaginationLimit,
)
from jobspy.linkedin.request import CIRCUIT_COOLDOWN_SECONDS, request_gate

from scripts.collection.checkpoint import QueryCheckpoint, QueryProgress, keep_best
from wecanfindintern.config import Settings
from wecanfindintern.db.pool import Database
from wecanfindintern.db.repositories.collection_cache import CollectionCacheRepository
from wecanfindintern.db.repositories.jobs import DedupeTimings, JobIngestionRepository
from wecanfindintern.domain.location import parse_location
from wecanfindintern.ingestion.collection_catalog import expand_collection_catalog
from wecanfindintern.ingestion.jobspy_adapter import (
    JobSpyQuery,
    NormalizedJob,
    fetch_linkedin_details,
    merge_linkedin_details,
    open_job_search,
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
            "failure_count": len(self.failures),
            "failures": list(self.failures),
        }


def is_retryable_collection_error(error: Exception) -> bool:
    """Classify ordinary query failures; LinkedIn cooldowns remain queued."""

    if isinstance(error, LinkedInRateLimitException):
        return False
    if isinstance(error, PaginationLimit):
        return False
    response = getattr(error, "response", None)
    if response is not None:
        return response.status_code in _RETRYABLE_HTTP_STATUSES or response.status_code >= 500
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


async def _wait_for_linkedin(error: LinkedInRateLimitException, item: str) -> None:
    delay = error.retry_after if error.retry_after > 0 else CIRCUIT_COOLDOWN_SECONDS
    log(f"LinkedIn cooldown {delay:.1f}s; keeping {item} queued", level="WARN")
    await asyncio.sleep(delay)


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
    stats = {"retried": 0, "succeeded": 0, "failed": 0, "skipped": 0}
    tasks = [
        (definition, source)
        for definition in definitions
        if definition.get("enabled", True)
        for source in definition["sites"]
    ]
    # A worker sleeping inside LinkedIn's HTTP gate must never occupy Indeed's only slot.
    has_linkedin = any(source == "linkedin" for _, source in tasks)
    indeed_slots = asyncio.Semaphore(max(1, concurrency - int(has_linkedin)))
    linkedin_lock = asyncio.Lock()
    newly_seen = set()
    restored_ids = set()
    if checkpoint is not None:
        for progress in checkpoint.queries.values():
            restored_ids.update(progress.jobs)
    started = time.monotonic()
    log(f"Starting collection campaign: {len(tasks)} site queries")

    async def fetch(read_page, source, cursor):
        retries = 0
        while True:
            try:
                if source == "linkedin":
                    async with linkedin_lock:
                        try:
                            return await asyncio.to_thread(read_page, cursor)
                        except LinkedInRateLimitException as error:
                            await _wait_for_linkedin(error, f"page {cursor or 'first'}")
                            stats["retried"] += 1
                    continue  # Let the next waiting LinkedIn page take its turn.
                async with indeed_slots:
                    return await asyncio.to_thread(read_page, cursor)
            except InvalidPageCursor:
                raise
            except Exception as error:
                if retries >= max_retries or not is_retryable_collection_error(error):
                    raise
                retries += 1
                stats["retried"] += 1
                delay = min(15.0, 1.5 * 2 ** (retries - 1) + random.uniform(0.5, 2))
                log(f"Retrying {source} page after {delay:.1f}s: {error}", level="WARN")
                await asyncio.sleep(delay)

    async def collect_query(definition, source):
        key = (definition["name"], source)
        state = (
            checkpoint.queries.setdefault(key, QueryProgress())
            if checkpoint is not None
            else QueryProgress()
        )
        values = dict(definition["query"])
        overrides = values.pop("source_overrides", {})
        values.update(overrides.get(source, {}))
        values = resolve_query_location(values, source)
        if hours_old_override is not None:
            values["hours_old"] = hours_old_override
        if source == "linkedin" and defer_linkedin_descriptions:
            values["linkedin_fetch_description"] = False
        query = JobSpyQuery.model_validate({**values, "sites": [source]})

        async def save(jobs, cursor, restart=False):
            # Commit this page and its continuation together before issuing another request.
            if checkpoint is not None:
                await asyncio.to_thread(
                    checkpoint.save_page, key, state.sequence, jobs, cursor, restart
                )
            state.advance(jobs, cursor, restart)

        if not state.complete:
            since = (
                checkpoint.started_at - timedelta(hours=query.hours_old)
                if checkpoint is not None and query.hours_old is not None
                else None
            )
            with open_job_search(query, posted_since=since) as read_page:
                restarted = False
                while not state.complete:
                    try:
                        page = await fetch(read_page, source, state.cursor)
                    except InvalidPageCursor as error:
                        await save([], None, restart=True)
                        if not restarted:
                            restarted = True
                            continue
                        failures.append(f"{key[0]}:{source}: {error}")
                        break
                    except PaginationLimit as error:
                        # Recheck the last accessible page on resume, never certify a hard cutoff.
                        await save([], state.last_cursor, restart=True)
                        failures.append(f"{key[0]}:{source}: {error}")
                        break
                    except Exception as error:
                        failures.append(f"{key[0]}:{source}: {type(error).__name__}: {error}")
                        break

                    jobs = [
                        job for job in page.jobs if _is_in_country_scope(job, query.country_indeed)
                    ]
                    newly_seen.update(job.source_fingerprint for job in jobs)
                    following = page.next_cursor
                    if following is not None and (
                        following == state.cursor or following in state.visited
                    ):
                        await save(jobs, None, restart=True)
                        failures.append(f"{key[0]}:{source}: repeated page cursor, end unconfirmed")
                        break
                    await save(jobs, following)

        stats["succeeded" if state.complete else "failed"] += 1
        for job in state.jobs.values():
            keep_best(collected, job)
        log(
            f"{key[0]} / {source}: {len(state.jobs)} unique jobs, "
            f"{'complete' if state.complete else 'pending'}"
        )

    await asyncio.gather(*(collect_query(definition, source) for definition, source in tasks))
    elapsed = max(time.monotonic() - started, 0.001)
    log(
        f"List throughput: {len(newly_seen - restored_ids) * 60 / elapsed:.2f} "
        "new unique source jobs/minute (excluding restored jobs)"
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
    checkpoint=None,
    max_retries: int = 3,
) -> tuple[list[NormalizedJob], dict[str, int]]:
    """Resolve each ID once per sweep; pending IDs are derived from the durable list cards."""
    linkedin = {job.source_fingerprint: job for job in jobs if job.source == "linkedin"}
    saved = checkpoint.details if checkpoint is not None else {}
    hydrated: dict[str, NormalizedJob] = {}
    removed = set()
    pending = []
    stats = dict.fromkeys(
        (
            "linkedin_detail_cache_hits",
            "linkedin_detail_fetched",
            "linkedin_detail_failed",
            "linkedin_detail_stale_fallbacks",
            "linkedin_detail_removed",
            "linkedin_detail_restored",
        ),
        0,
    )

    for fingerprint, job in linkedin.items():
        if fingerprint in saved:
            previous = saved[fingerprint]
            if previous is None:
                removed.add(fingerprint)
                continue
            if _linkedin_card_matches_cache(job, previous.raw):
                hydrated[fingerprint] = merge_linkedin_details(
                    job, previous.raw, fetched_at=previous.details_fetched_at
                )
                stats["linkedin_detail_restored"] += 1
                continue
        pending.append(fingerprint)

    cache_entries = await cache.linkedin_details(pending) if pending else {}
    fresh_since = datetime.now(UTC) - timedelta(seconds=max(0, ttl_seconds))
    work = deque(pending)

    async def resolved(fingerprint, job):
        if checkpoint is not None:
            await asyncio.to_thread(checkpoint.save_detail, fingerprint, job)
            saved[fingerprint] = job
        if job is None:
            removed.add(fingerprint)
        else:
            hydrated[fingerprint] = job

    async def fetch(fingerprint):
        job = linkedin[fingerprint]
        entry = cache_entries.get(fingerprint)
        if (
            entry
            and entry.details_fetched_at >= fresh_since
            and _linkedin_card_matches_cache(job, entry.payload)
        ):
            await resolved(
                fingerprint,
                merge_linkedin_details(job, entry.payload, fetched_at=entry.details_fetched_at),
            )
            stats["linkedin_detail_cache_hits"] += 1
            return
        retries = 0
        detail_key = job.source_job_id or urlsplit(job.source_url).path
        reason = "missing posting link"
        while detail_key:
            try:
                details = await asyncio.to_thread(fetch_linkedin_details, detail_key)
            except LinkedInJobGone as error:
                await resolved(fingerprint, None)
                log(str(error))
                return
            except LinkedInRateLimitException as error:
                await _wait_for_linkedin(error, f"details for {job.source_job_id}")
                work.append(fingerprint)
                return
            except Exception as error:
                reason = str(error)
                if retries < max_retries and is_retryable_collection_error(error):
                    retries += 1
                    await asyncio.sleep(min(15, 2 ** (retries - 1) * 1.5))
                    continue
                break
            if str(details.get("description") or "").strip():
                await resolved(fingerprint, merge_linkedin_details(job, details))
                stats["linkedin_detail_fetched"] += 1
                return
            reason = "empty description"
            break
        if entry is not None and _linkedin_card_matches_cache(job, entry.payload):
            hydrated[fingerprint] = merge_linkedin_details(
                job, entry.payload, fetched_at=entry.details_fetched_at
            )
            stats["linkedin_detail_stale_fallbacks"] += 1
        stats["linkedin_detail_failed"] += 1
        log(f"Pending LinkedIn detail {job.source_job_id}: {reason}", level="WARN")

    # Bound active coroutines as well as network requests when a full search finds many IDs.

    async def worker():
        while work:
            await fetch(work.popleft())

    await asyncio.gather(*(worker() for _ in range(min(len(pending), max(1, concurrency)))))
    stats["linkedin_detail_removed"] = len(removed)
    return [
        hydrated.get(job.source_fingerprint, job)
        for job in jobs
        if job.source_fingerprint not in removed
    ], stats


def _linkedin_card_matches_cache(job: NormalizedJob, payload: dict[str, Any]) -> bool:
    for field in ("title", "company", "location"):
        current = job.raw.get(field)
        previous = payload.get(field)
        if current not in (None, "") and previous not in (None, "") and current != previous:
            return False
    return True


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

    settings = Settings.from_env()
    database = Database(settings)
    await database.open()
    repository: JobIngestionRepository | None = None
    run_record = None
    counts: Counter[str] = Counter()
    failures: list[str] = []
    query_stats: dict[str, int] = {}
    result: CampaignResult | None = None
    resources = ExitStack()
    try:
        if checkpoint is None:
            # Coordinate local collectors, including two processes using the same local store.
            guard = resources.enter_context(psycopg.connect(settings.database_url, autocommit=True))
            if not guard.execute("SELECT pg_try_advisory_lock(790341567)").fetchone()[0]:
                raise RuntimeError("Another collection campaign is running")
            checkpoint = QueryCheckpoint.local(
                Path(os.getenv("WCFI_LOG_DIR", "logs")), config_path, settings.database_url
            )
            resources.callback(checkpoint.db.close)
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

        checkpoint.restore(sweep_number)
        resources.enter_context(
            request_gate.campaign(checkpoint.retry_at, checkpoint.save_cooldown)
        )
        by_source = {}
        for definition in definitions:
            if definition.get("enabled", True):
                for source in definition["sites"]:
                    by_source.setdefault(source, []).append({**definition, "sites": [source]})

        repository = JobIngestionRepository(database.pool)
        run_record = await repository.start_run(
            sources=sorted(by_source),
            query={
                "campaign": config_path.name,
                "plan_count": len(definitions),
                "sweep_number": sweep_number,
                "sweep_mode": sweep_mode,
                "recent_hours": hours_old,
                "native_pages": True,
            },
        )
        query_counts = Counter(retried=0, succeeded=0, failed=0, skipped=0)

        async def collect_source(source, plans):
            want_details = source == "linkedin" and linkedin_details_enabled(plans)
            slots = concurrency - int(source == "indeed" and "linkedin" in by_source)
            jobs, issues, statistics = await collect_all(
                plans,
                concurrency=max(1, slots),
                max_retries=max_retries,
                defer_linkedin_descriptions=want_details,
                hours_old_override=hours_old,
                checkpoint=checkpoint,
            )
            query_counts.update(statistics)
            failures.extend(issues)
            if want_details:
                jobs, details = await hydrate_linkedin_descriptions(
                    jobs,
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
                    checkpoint=checkpoint,
                    max_retries=max_retries,
                )
                query_counts.update(details)
                if details["linkedin_detail_failed"]:
                    failures.append(
                        f"linkedin: {details['linkedin_detail_failed']} pending job descriptions"
                    )
                log(f"LinkedIn details: {details}")

            return jobs

        tasks = [
            asyncio.create_task(collect_source(source, plans))
            for source, plans in by_source.items()
        ]
        try:
            source_results = await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        round_jobs = [job for jobs in source_results for job in jobs]
        timings = DedupeTimings()
        started = time.monotonic()
        dedupe_seconds = {}

        def record_timings(_outcomes):
            dedupe_seconds.update(timings.summary(time.monotonic() - started))

        pipeline = await run_ingestion_pipeline(
            pool=database.pool,
            run_id=run_record.internal_id,
            jobs=round_jobs,
            scraped_at=checkpoint.attempt_at,
            observed_at=checkpoint.observed,
            batch_size=batch_size,
            outcomes=counts,
            timings=timings,
            after_persist=record_timings,
        )
        total_jobs = pipeline.unique_jobs
        query_stats = dict(query_counts)
        query_stats.update(
            {
                "sweep_number": sweep_number,
                "recent_hours": hours_old or 0,
                "full_sweep": int(hours_old is None),
            }
        )
        log(f"Dedupe timing (seconds): {dict(dedupe_seconds)}")
        await repository.finish_run(
            run_record.internal_id,
            failed_count=len(failures),
            error_summary="\n".join(failures)[:2000] or None,
            partial=bool(failures),
        )
        duration = time.monotonic() - start_time
        result_status = (
            "failed" if failures and not total_jobs else "partial" if failures else "success"
        )
        result = CampaignResult(
            completed_at=datetime.now(UTC),
            duration_seconds=duration,
            status=result_status,
            unique_jobs_collected=total_jobs,
            dedupe_seconds=dedupe_seconds,
            database_stats={
                "created": counts["created"],
                "merged": counts["merged"],
                "updated": counts["updated"],
                "unchanged": counts["unchanged"],
            },
            salary_stats=dataclasses.asdict(pipeline.salary),
            recruiting_term_stats={
                "regex": pipeline.recruiting_term.regex,
                "not_found": pipeline.recruiting_term.not_found,
                "cached": pipeline.recruiting_term.skipped_cached,
                "failed": pipeline.recruiting_term.failed,
            },
            query_stats=query_stats,
            failures=tuple(failures),
        )
        summary_path = Path(os.getenv("WCFI_LOG_DIR", "logs")) / "campaign_summary_latest.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(result.payload(), indent=2), encoding="utf-8")
        if result.status == "success":
            checkpoint.clear()
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
        resources.close()
        await database.close()

    if failures:
        log(f"Source failures encountered: {len(failures)}", level="WARN")
        for failure in failures:
            log(f"- {failure}", level="WARN")
    if result is None:
        raise RuntimeError("collection campaign ended without a result")
    return result
