#!/usr/bin/env python3
"""Backfill persisted recruiting seasons for all active jobs."""

from __future__ import annotations

import asyncio
import argparse

from wecanfindintern.config import Settings
from wecanfindintern.db.ingestion_repository import JobIngestionRepository
from wecanfindintern.db.pool import Database
from wecanfindintern.ingestion.recruiting_term_enrichment import enrich_recruiting_terms


async def backfill(*, allow_llm: bool) -> None:
    database = Database(Settings.from_env())
    await database.open()
    try:
        stats = await enrich_recruiting_terms(
            JobIngestionRepository(database.pool),
            allow_llm=allow_llm,
        )
    finally:
        await database.close()
    print(
        "recruiting term backfill complete: "
        f"regex={stats.regex}, deepseek={stats.llm}, not_found={stats.not_found}, "
        f"cached={stats.skipped_cached}, failed={stats.failed}"
        f", llm_deferred={stats.llm_deferred}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--regex-only",
        action="store_true",
        help="finish the global regex pass without starting DeepSeek requests",
    )
    args = parser.parse_args()
    asyncio.run(backfill(allow_llm=not args.regex_only))


if __name__ == "__main__":
    main()
