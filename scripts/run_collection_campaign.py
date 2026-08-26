#!/usr/bin/env python3
"""Collect every configured query first, then dedupe, regex, and DeepSeek."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wecanfindintern.config import Settings
from wecanfindintern.db.ingestion_repository import JobIngestionRepository
from wecanfindintern.db.pool import Database
from wecanfindintern.domain.jobs import canonical_job_from_jobspy, parse_location
from wecanfindintern.ingestion.jobspy_adapter import JobSpyQuery, NormalizedJob
from wecanfindintern.ingestion.collection_catalog import expand_collection_catalog
from wecanfindintern.ingestion.location_query import resolve_query_location
from wecanfindintern.ingestion.salary_enrichment import enrich_missing_salaries
from wecanfindintern.scheduler.runner import scrape_checked


def chunks(items: list, size: int):
    for offset in range(0, len(items), size):
        yield items[offset : offset + size]


async def collect_all(definitions: list[dict[str, Any]]) -> tuple[list[NormalizedJob], list[str]]:
    collected: dict[str, NormalizedJob] = {}
    failures: list[str] = []
    for definition in definitions:
        if not definition.get("enabled", True):
            continue
        for source in definition["sites"]:
            offset = 0
            seen_for_query: set[str] = set()
            maximum = definition.get("max_results_per_source", 50)
            page_size = definition.get("page_size", 25)
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
                try:
                    _, result = await asyncio.to_thread(scrape_checked, query)
                except Exception as error:
                    failures.append(
                        f"{definition['name']}:{source}:{type(error).__name__}: {error}"
                    )
                    break
                if not result.jobs:
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
                for job in new_jobs:
                    existing = collected.get(job.source_fingerprint)
                    if existing is None or _record_score(job) > _record_score(existing):
                        collected[job.source_fingerprint] = job
                offset += len(result.jobs)
            print(
                f"collected {definition['name']} / {source}: "
                f"{len(seen_for_query)} source jobs"
            )
    return list(collected.values()), failures


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


async def run(config_path: Path, batch_size: int) -> None:
    definitions = expand_collection_catalog(
        json.loads(config_path.read_text(encoding="utf-8"))
    )
    # Stage 1: every network collection completes before any database dedupe begins.
    normalized_jobs, failures = await collect_all(definitions)
    print(f"collection stage complete: {len(normalized_jobs)} unique source records")

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
        print(
            "dedupe stage complete: "
            f"created={counts['created']}, merged={counts['merged']}, "
            f"unchanged={counts['unchanged']}"
        )

        # Stages 3 and 4 are globally separated inside this function.
        salary = await enrich_missing_salaries(repository, normalized_jobs)
        print(
            "salary stages complete: "
            f"source={salary.structured}, regex={salary.regex}, deepseek={salary.llm}"
        )
        await repository.finish_run(
            run_record.internal_id,
            failed_count=len(failures),
            error_summary="\n".join(failures)[:2000] or None,
            partial=bool(failures),
        )
        plan_names = [definition["name"] for definition in definitions]
        async with database.pool.connection() as connection:
            await connection.execute(
                """
                UPDATE collection_plans
                SET active_run_id = NULL,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_completed_at = now(),
                    next_run_at = now() + make_interval(secs => interval_seconds),
                    updated_at = now()
                WHERE name = ANY(%s)
                """,
                (plan_names,),
            )
    except Exception as error:
        await repository.finish_run(
            run_record.internal_id,
            failed_count=max(1, len(failures)),
            error_summary=str(error)[:2000],
            partial=bool(counts),
        )
        raise
    finally:
        await database.close()

    if failures:
        print(f"source failures: {len(failures)}")
        for failure in failures:
            print(f"- {failure}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/collection_plans.json"))
    parser.add_argument("--batch-size", type=int, default=250)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 500:
        parser.error("--batch-size must be between 1 and 500")
    asyncio.run(run(args.config, args.batch_size))


if __name__ == "__main__":
    main()
