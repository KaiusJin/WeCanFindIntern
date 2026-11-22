#!/usr/bin/env python3
"""Orchestrate one campaign: plan, collect every source, then persist, dedupe and enrich."""

from __future__ import annotations

import dataclasses
import json
import os
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cloud.console import log
from cloud.ops.monitor import build_source_monitor, source_group
from cloud.pipeline.ingest import run_ingestion_pipeline
from cloud.planning.collection_catalog import expand_collection_catalog
from cloud.planning.sweep import collection_hours_window
from cloud.sources.job_boards.simplify import gap_fill_jobs
from cloud.sources.job_boards.stage import collect_job_boards
from cloud.sources.jobspy.stage import (
    collect_queries,
    hydrate_linkedin_descriptions,
    linkedin_details_enabled,
)
from cloud.storage.collection_cache import CollectionCacheRepository
from cloud.storage.jobs import DedupeTimings, JobIngestionRepository
from wecanfindintern.config import Settings
from wecanfindintern.db.pool import Database


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
        normalized_jobs, failures, query_stats = await collect_queries(
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
        ats_jobs, ats_stats, simplify_listings = await collect_job_boards(config_path, defaults)
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
                        for source, n in Counter(
                            source_group(job) for job in normalized_jobs
                        ).items()
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
