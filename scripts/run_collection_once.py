#!/usr/bin/env python3
"""Claim and execute at most one due collection plan."""

from __future__ import annotations

import argparse
import asyncio

from wecanfindintern.config import Settings
from wecanfindintern.db.ingestion_repository import JobIngestionRepository
from wecanfindintern.db.pool import Database
from wecanfindintern.scheduler.repository import CollectionRepository
from wecanfindintern.scheduler.runner import CollectionRunner


async def execute(worker_id: str | None, lease_seconds: int) -> bool:
    database = Database(Settings.from_env())
    await database.open()
    try:
        runner = CollectionRunner(
            CollectionRepository(database.pool),
            JobIngestionRepository(database.pool),
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )
        return await runner.run_once()
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-id")
    parser.add_argument("--lease-seconds", type=int, default=900)
    args = parser.parse_args()
    handled = asyncio.run(execute(args.worker_id, args.lease_seconds))
    print("已执行一个到期计划" if handled else "当前没有到期计划")


if __name__ == "__main__":
    main()
