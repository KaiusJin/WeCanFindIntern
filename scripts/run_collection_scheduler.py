#!/usr/bin/env python3
"""Continuously execute due plans; schedule timing is persisted in PostgreSQL."""

from __future__ import annotations

import argparse
import asyncio

from wecanfindintern.config import Settings
from wecanfindintern.db.ingestion_repository import JobIngestionRepository
from wecanfindintern.db.pool import Database
from wecanfindintern.scheduler.repository import CollectionRepository
from wecanfindintern.scheduler.runner import CollectionRunner


async def serve(worker_id: str | None, lease_seconds: int, poll_seconds: int) -> None:
    database = Database(Settings.from_env())
    await database.open()
    try:
        runner = CollectionRunner(
            CollectionRepository(database.pool),
            JobIngestionRepository(database.pool),
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )
        while True:
            handled = await runner.run_once()
            if not handled:
                await asyncio.sleep(poll_seconds)
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-id")
    parser.add_argument("--lease-seconds", type=int, default=900)
    parser.add_argument("--poll-seconds", type=int, default=30)
    args = parser.parse_args()
    if args.poll_seconds < 1:
        parser.error("--poll-seconds must be at least 1")
    asyncio.run(serve(args.worker_id, args.lease_seconds, args.poll_seconds))


if __name__ == "__main__":
    main()
