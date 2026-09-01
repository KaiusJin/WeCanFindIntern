#!/usr/bin/env python3
"""Collect every configured query first, then dedupe, regex, and DeepSeek."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import signal
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wecanfindintern.config import Settings
from wecanfindintern.db.pool import Database
from wecanfindintern.db.repositories.jobs import JobIngestionRepository
from wecanfindintern.domain.location import parse_location
from wecanfindintern.ingestion.collection_catalog import expand_collection_catalog
from wecanfindintern.ingestion.jobspy_adapter import (
    JobSpyQuery,
    NormalizedJob,
    scrape_checked,
)
from wecanfindintern.ingestion.location_query import resolve_query_location
from wecanfindintern.ingestion.pipeline import run_ingestion_pipeline

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


_RETRYABLE_HTTP_STATUSES = {408, 425, 429}
_PERMANENT_ERROR_MARKERS = ("location not parsed",)


@dataclass(frozen=True, slots=True)
class CampaignResult:
    """Structured result shared by CLI summaries and the desktop status API."""

    completed_at: datetime
    duration_seconds: float
    status: str
    unique_jobs_collected: int
    database_stats: dict[str, int]
    salary_stats: dict[str, int]
    recruiting_term_stats: dict[str, int]
    query_stats: dict[str, int]
    failures: tuple[str, ...]

    def payload(self) -> dict[str, Any]:
        return {
            "completed_at": self.completed_at.isoformat(),
            "duration_seconds": round(self.duration_seconds, 2),
            "status": self.status,
            "unique_jobs_collected": self.unique_jobs_collected,
            "database_stats": dict(self.database_stats),
            "salary_stats": dict(self.salary_stats),
            "recruiting_term_stats": dict(self.recruiting_term_stats),
            "query_stats": dict(self.query_stats),
            "failure_count": len(self.failures),
            "failures": list(self.failures),
        }


def is_retryable_collection_error(error: Exception) -> bool:
    """Retry transient failures, but stop immediately for deterministic provider errors."""

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


def acquire_process_lock(lock_file) -> None:
    """Acquire a non-blocking process lock on Unix or Windows."""

    if sys.platform == "win32":
        try:
            lock_file.seek(0)
            if not lock_file.read(1):
                lock_file.seek(0)
                lock_file.write("0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise BlockingIOError from exc
    else:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def log(message: str, level: str = "INFO") -> None:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] [{level}] {message}", flush=True)


async def collect_all(
    definitions: list[dict[str, Any]],
    *,
    concurrency: int = 4,
    max_retries: int = 3,
) -> tuple[list[NormalizedJob], list[str], dict[str, int]]:
    collected: dict[str, NormalizedJob] = {}
    failures: list[str] = []
    stats = {"retried": 0, "succeeded": 0, "failed": 0, "skipped": 0}
    disabled_sources: set[str] = set()
    lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(max(1, concurrency))

    tasks: list[tuple[dict[str, Any], str]] = []
    for definition in definitions:
        if not definition.get("enabled", True):
            continue
        for source in definition["sites"]:
            tasks.append((definition, source))

    total_tasks = len(tasks)
    log(f"Starting collection campaign: {total_tasks} site queries with concurrency={concurrency}")

    async def _collect_single_query(definition: dict[str, Any], source: str, task_idx: int) -> None:
        async with semaphore:
            async with lock:
                source_disabled = source in disabled_sources
                if source_disabled:
                    stats["skipped"] += 1
            if source_disabled:
                log(
                    f"[{task_idx}/{total_tasks}] Skipped {definition['name']} / {source}: "
                    "provider circuit is open",
                    level="WARN",
                )
                return

            offset = 0
            seen_for_query: set[str] = set()
            maximum = definition.get("max_results_per_source", 50)
            page_size = definition.get("page_size", 25)
            query_jobs: list[NormalizedJob] = []
            query_has_failed = False

            while offset < maximum:
                requested = min(page_size, maximum - offset)
                values = dict(definition["query"])
                overrides = values.pop("source_overrides", {})
                values.update(overrides.get(source, {}))
                values = resolve_query_location(values, source)
                google_term = values.get("google_search_term")
                if google_term:
                    values["google_search_term"] = google_term.format(
                        search_term=values["search_term"],
                        location=values.get("location") or "Canada",
                    )
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
                                f"[{task_idx}/{total_tasks}] FAILED permanently; "
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

            async with lock:
                if query_has_failed:
                    stats["failed"] += 1
                else:
                    stats["succeeded"] += 1
                for job in query_jobs:
                    existing = collected.get(job.source_fingerprint)
                    if existing is None or _record_score(job) > _record_score(existing):
                        collected[job.source_fingerprint] = job

            if not query_has_failed:
                log(
                    f"[{task_idx}/{total_tasks}] Collected {definition['name']} / {source}: "
                    f"{len(seen_for_query)} source jobs (unique total: {len(collected)})"
                )

    await asyncio.gather(
        *[_collect_single_query(defn, src, idx) for idx, (defn, src) in enumerate(tasks, start=1)]
    )
    return list(collected.values()), failures, stats


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
) -> CampaignResult:
    start_time = time.time()
    definitions = expand_collection_catalog(json.loads(config_path.read_text(encoding="utf-8")))

    # Stage 1: every network collection completes before any database dedupe begins.
    normalized_jobs, failures, query_stats = await collect_all(
        definitions, concurrency=concurrency, max_retries=max_retries
    )
    log(
        f"Collection stage complete: {len(normalized_jobs)} unique source records "
        f"(queries succeeded={query_stats['succeeded']}, "
        f"failed={query_stats['failed']}, retried={query_stats['retried']})"
    )

    database = Database(Settings.from_env())
    await database.open()
    repository = JobIngestionRepository(database.pool)
    sources = sorted({job.source for job in normalized_jobs})
    run_record = await repository.start_run(
        sources=sources,
        query={"campaign": config_path.name, "plan_count": len(definitions)},
    )
    counts: Counter[str] = Counter()
    scraped_at = datetime.now(UTC)
    try:
        pipeline = await run_ingestion_pipeline(
            pool=database.pool,
            run_id=run_record.internal_id,
            jobs=normalized_jobs,
            scraped_at=scraped_at,
            batch_size=batch_size,
            outcomes=counts,
            after_persist=lambda persisted: log(
                f"Dedupe stage complete: created={persisted['created']}, "
                f"merged={persisted['merged']}, unchanged={persisted['unchanged']}"
            ),
        )

        # Stages 3 and 4: LLM Enrichments
        salary = pipeline.salary
        log(
            f"Salary stages complete: source={salary.structured}, "
            f"regex={salary.regex}, deepseek={salary.llm}"
        )

        term = pipeline.recruiting_term
        log(
            f"Recruiting term stages complete: regex={term.regex}, "
            f"deepseek={term.llm}, not_found={term.not_found}, "
            f"cached={term.skipped_cached}, failed={term.failed}"
        )

        await repository.finish_run(
            run_record.internal_id,
            failed_count=len(failures),
            error_summary="\n".join(failures)[:2000] or None,
            partial=bool(failures),
        )
        duration = time.time() - start_time
        result_status = (
            "failed" if failures and not normalized_jobs else "partial" if failures else "success"
        )
        result = CampaignResult(
            completed_at=datetime.now(UTC),
            duration_seconds=duration,
            status=result_status,
            unique_jobs_collected=len(normalized_jobs),
            database_stats={
                "created": counts["created"],
                "merged": counts["merged"],
                "updated": counts["updated"],
                "unchanged": counts["unchanged"],
            },
            salary_stats={
                "structured": salary.structured,
                "regex": salary.regex,
                "deepseek": salary.llm,
            },
            recruiting_term_stats={
                "regex": term.regex,
                "deepseek": term.llm,
                "not_found": term.not_found,
                "cached": term.skipped_cached,
                "failed": term.failed,
            },
            query_stats=query_stats,
            failures=tuple(failures),
        )
        summary_path = Path(os.getenv("WCFI_LOG_DIR", "logs")) / "campaign_summary_latest.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(result.payload(), indent=2), encoding="utf-8")
        log(f"Collection campaign finished in {duration:.1f}s. Summary written to {summary_path}")

    except Exception as error:
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
    return result


def main() -> None:
    resource_dir = Path(os.getenv("WCFI_RESOURCE_DIR", "."))
    runtime_dir = Path(os.getenv("WCFI_RUNTIME_DIR", ".runtime"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=resource_dir / "config" / "collection_plans.json"
    )
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Max concurrent scraper threads/queries",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max retries per scraper query on error",
    )
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=runtime_dir / "collection-campaign.lock",
        help="Process lock shared by manual and scheduled campaign runs",
    )
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 500:
        parser.error("--batch-size must be between 1 and 500")
    if not 1 <= args.concurrency <= 16:
        parser.error("--concurrency must be between 1 and 16")
    if not 0 <= args.max_retries <= 10:
        parser.error("--max-retries must be between 0 and 10")

    args.lock_file.parent.mkdir(parents=True, exist_ok=True)

    # Graceful signal handler for unexpected termination
    def _handle_signal(signum, _frame):
        sig_name = signal.Signals(signum).name
        log(f"Received termination signal ({sig_name}). Exiting cleanly...", level="WARN")
        sys.exit(128 + signum)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    with args.lock_file.open("a+", encoding="utf-8") as lock_file:
        try:
            acquire_process_lock(lock_file)
        except BlockingIOError:
            log(f"Campaign already running; lock is held at {args.lock_file}", level="WARN")
            return
        asyncio.run(
            run(
                args.config,
                args.batch_size,
                concurrency=args.concurrency,
                max_retries=args.max_retries,
            )
        )


if __name__ == "__main__":
    main()
