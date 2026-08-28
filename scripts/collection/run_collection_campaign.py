#!/usr/bin/env python3
"""Collect every configured query first, then dedupe, regex, and DeepSeek."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import random
import signal
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wecanfindintern.config import Settings
from wecanfindintern.db.pool import Database
from wecanfindintern.db.repositories.jobs import JobIngestionRepository
from wecanfindintern.db.repositories.recruiting_term import RecruitingTermRepository
from wecanfindintern.db.repositories.salary import SalaryRepository
from wecanfindintern.domain.jobs import canonical_job_from_jobspy, parse_location
from wecanfindintern.ingestion.collection_catalog import expand_collection_catalog
from wecanfindintern.ingestion.jobspy_adapter import (
    JobSpyQuery,
    NormalizedJob,
    scrape_checked,
)
from wecanfindintern.ingestion.location_query import resolve_query_location
from wecanfindintern.ingestion.recruiting_term_enrichment import enrich_recruiting_terms
from wecanfindintern.ingestion.salary_enrichment import enrich_missing_salaries


def log(message: str, level: str = "INFO") -> None:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] [{level}] {message}", flush=True)


def chunks(items: list, size: int):
    for offset in range(0, len(items), size):
        yield items[offset : offset + size]


async def collect_all(
    definitions: list[dict[str, Any]],
    *,
    concurrency: int = 4,
    max_retries: int = 3,
) -> tuple[list[NormalizedJob], list[str], dict[str, int]]:
    collected: dict[str, NormalizedJob] = {}
    failures: list[str] = []
    stats = {"retried": 0, "succeeded": 0, "failed": 0}
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
                attempt = 0
                result = None
                while attempt <= max_retries:
                    try:
                        _, result = await asyncio.to_thread(scrape_checked, query)
                        break
                    except Exception as error:
                        attempt += 1
                        async with lock:
                            stats["retried"] += 1
                        if attempt > max_retries:
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
                        backoff = min(
                            15.0, (2 ** (attempt - 1)) * 1.5 + random.uniform(0.5, 2.0)
                        )
                        log(
                            f"[{task_idx}/{total_tasks}] Retry {attempt}/{max_retries} "
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

    await asyncio.gather(*[
        _collect_single_query(defn, src, idx)
        for idx, (defn, src) in enumerate(tasks, start=1)
    ])
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
) -> None:
    start_time = time.time()
    definitions = expand_collection_catalog(
        json.loads(config_path.read_text(encoding="utf-8"))
    )

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
        # Stage 2: build salary-free records and deduplicate the complete campaign.
        canonical_jobs = [
            await asyncio.to_thread(
                canonical_job_from_jobspy,
                job,
                scraped_at=scraped_at,
                allow_salary_extraction=False,
            )
            for job in normalized_jobs
        ]
        for batch in chunks(canonical_jobs, batch_size):
            counts.update(
                await repository.ingest_batch(
                    run_id=run_record.internal_id,
                    jobs=batch,
                    scraped_at=scraped_at,
                )
            )
        log(
            f"Dedupe stage complete: created={counts['created']}, "
            f"merged={counts['merged']}, unchanged={counts['unchanged']}"
        )

        # Stages 3 and 4: LLM Enrichments
        salary = await enrich_missing_salaries(
            SalaryRepository(database.pool), normalized_jobs
        )
        log(
            f"Salary stages complete: source={salary.structured}, "
            f"regex={salary.regex}, deepseek={salary.llm}"
        )

        term = await enrich_recruiting_terms(
            RecruitingTermRepository(database.pool),
            [job.source_fingerprint for job in normalized_jobs],
        )
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
        summary_payload = {
            "completed_at": datetime.now(UTC).isoformat(),
            "duration_seconds": round(duration, 2),
            "status": "partial" if failures else "success",
            "unique_jobs_collected": len(normalized_jobs),
            "database_stats": {
                "created": counts["created"],
                "merged": counts["merged"],
                "unchanged": counts["unchanged"],
            },
            "salary_stats": {
                "structured": salary.structured,
                "regex": salary.regex,
                "deepseek": salary.llm,
            },
            "recruiting_term_stats": {
                "regex": term.regex,
                "deepseek": term.llm,
                "not_found": term.not_found,
                "cached": term.skipped_cached,
                "failed": term.failed,
            },
            "query_stats": query_stats,
            "failures": failures,
        }
        summary_path = Path("logs/campaign_summary_latest.json")
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path("config/collection_plans.json")
    )
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument(
        "--concurrency", type=int, default=4,
        help="Max concurrent scraper threads/queries",
    )
    parser.add_argument(
        "--max-retries", type=int, default=3,
        help="Max retries per scraper query on error",
    )
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=Path(".runtime/collection-campaign.lock"),
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
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
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
