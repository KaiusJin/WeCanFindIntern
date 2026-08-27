#!/usr/bin/env python3
"""Run JobSpy and ingest stable jobs into PostgreSQL with deduplication."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import UTC, datetime

from _cli import add_query_arguments, query_from_args

from wecanfindintern.config import Settings
from wecanfindintern.db.ingestion_repository import JobIngestionRepository
from wecanfindintern.db.pool import Database
from wecanfindintern.domain.jobs import canonical_job_from_jobspy
from wecanfindintern.ingestion.salary_enrichment import enrich_missing_salaries
from wecanfindintern.ingestion.recruiting_term_enrichment import enrich_recruiting_terms
from wecanfindintern.ingestion.jobspy_adapter import scrape_and_normalize


def chunks(items: list, size: int):
    for offset in range(0, len(items), size):
        yield items[offset : offset + size]


async def persist(query, normalized_jobs, scraped_at: datetime, batch_size: int) -> None:
    database = Database(Settings.from_env())
    await database.open()
    repository = JobIngestionRepository(database.pool)
    run = await repository.start_run(
        sources=query.sites,
        query=query.model_dump(mode="json", exclude={"proxies", "ca_cert"}),
    )
    counts: Counter[str] = Counter()
    try:
        canonical_jobs = []
        for job in normalized_jobs:
            canonical = await asyncio.to_thread(
                canonical_job_from_jobspy,
                job,
                scraped_at=scraped_at,
                allow_salary_extraction=False,
            )
            canonical_jobs.append(canonical)
        for batch in chunks(canonical_jobs, batch_size):
            counts.update(
                await repository.ingest_batch(
                    run_id=run.internal_id,
                    jobs=batch,
                    scraped_at=scraped_at,
                )
            )
        salary_stats = await enrich_missing_salaries(repository, normalized_jobs)
        term_stats = await enrich_recruiting_terms(
            repository,
            [job.source_fingerprint for job in normalized_jobs],
        )
        await repository.finish_run(run.internal_id)
    except Exception as error:
        await repository.finish_run(
            run.internal_id,
            failed_count=len(normalized_jobs) - sum(counts.values()),
            error_summary=str(error)[:2000],
            partial=bool(counts),
        )
        raise
    finally:
        await database.close()

    print(f"采集批次: {run.public_id}")
    print(
        "写入结果: "
        f"created={counts['created']}, merged={counts['merged']}, "
        f"unchanged={counts['unchanged']}"
    )
    print(
        "薪资处理: "
        f"source={salary_stats.structured}, regex={salary_stats.regex}, "
        f"deepseek={salary_stats.llm}"
    )
    print(
        "招聘季节处理: "
        f"regex={term_stats.regex}, deepseek={term_stats.llm}, "
        f"not_found={term_stats.not_found}, cached={term_stats.skipped_cached}, "
        f"failed={term_stats.failed}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_query_arguments(parser)
    parser.add_argument("--batch-size", type=int, default=250)
    args = parser.parse_args()
    if not 50 <= args.batch_size <= 500:
        parser.error("--batch-size 必须在 50 到 500 之间")

    query = query_from_args(args)
    scraped_at = datetime.now(UTC)
    _, result = scrape_and_normalize(query)
    asyncio.run(persist(query, result.jobs, scraped_at, args.batch_size))


if __name__ == "__main__":
    main()
