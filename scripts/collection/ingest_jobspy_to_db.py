#!/usr/bin/env python3
"""Run JobSpy and ingest stable jobs into PostgreSQL with deduplication."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import UTC, datetime

from wecanfindintern.config import Settings
from wecanfindintern.db.pool import Database
from wecanfindintern.db.repositories.jobs import JobIngestionRepository
from wecanfindintern.ingestion.jobspy_adapter import scrape_and_normalize
from wecanfindintern.ingestion.jobspy_cli import add_query_arguments, query_from_args
from wecanfindintern.ingestion.pipeline import run_ingestion_pipeline


async def persist(query, normalized_jobs, scraped_at: datetime, batch_size: int) -> None:
    database = Database(Settings.from_env())
    await database.open()
    repository = JobIngestionRepository(database.pool)
    run = await repository.start_run(
        sources=query.sites,
        query=query.model_dump(mode="json", exclude={"proxies", "ca_cert"}),
    )
    counts: Counter[str] = Counter()
    report = None
    try:
        report = await run_ingestion_pipeline(
            pool=database.pool,
            run_id=run.internal_id,
            jobs=normalized_jobs,
            scraped_at=scraped_at,
            batch_size=batch_size,
            outcomes=counts,
        )
        await repository.finish_run(run.internal_id)
    except Exception as error:
        processed = sum(counts.values())
        await repository.finish_run(
            run.internal_id,
            failed_count=len(normalized_jobs) - processed,
            error_summary=str(error)[:2000],
            partial=processed > 0,
        )
        raise
    finally:
        await database.close()

    assert report is not None
    print(f"采集批次: {run.public_id}")
    print(
        "写入结果: "
        f"created={report.outcomes['created']}, merged={report.outcomes['merged']}, "
        f"updated={report.outcomes['updated']}, "
        f"unchanged={report.outcomes['unchanged']}"
    )
    print(
        "薪资处理: "
        f"source={report.salary.structured}, regex={report.salary.regex}, "
        f"deepseek={report.salary.llm}"
    )
    print(
        "招聘季节处理: "
        f"regex={report.recruiting_term.regex}, deepseek={report.recruiting_term.llm}, "
        f"not_found={report.recruiting_term.not_found}, "
        f"cached={report.recruiting_term.skipped_cached}, "
        f"failed={report.recruiting_term.failed}"
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
