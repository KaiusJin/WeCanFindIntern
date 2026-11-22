"""Entry point: run one campaign, then maintain the database and publish the R2 feed."""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import psycopg

from cloud.campaign import run
from cloud.config import DEFAULT_PLAN_PATH
from cloud.ops.report import publish_ops, redact, write_step_summary
from cloud.ops.stats import collect_db_stats
from cloud.publish.feed import publish, r2_from_env
from cloud.storage.checkpoint import QueryCheckpoint
from cloud.storage.maintenance import acquire_campaign_lock, maintain

CONFIG = DEFAULT_PLAN_PATH


def should_expire(result) -> bool:
    """Close jobs unseen for 14 days only when this run saw every source.

    A single-process run must fully succeed. A parallel run tolerates query-level
    failures (an occasional LinkedIn 429) but not a missing shard: a shard that
    never ran leaves its jobs unrefreshed, so expiring would close live postings.
    """
    if result.status == "success":
        return True
    shards = (result.monitor or {}).get("shards")
    if result.status != "partial" or not shards:
        return False
    return not any(shard.get("missing") for shard in shards.values())


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m cloud.run")
    parser.add_argument(
        "--from-artifacts",
        type=Path,
        help="publish the shards collected by cloud.parallel instead of collecting here",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    # These are workflow-only credentials. Desktop code imports neither boto3 nor this module.
    url = os.environ["DATABASE_URL"]
    store = r2_from_env()
    with psycopg.connect(url, autocommit=True) as db:
        run_started_at = acquire_campaign_lock(db)
        started = time.monotonic()

        def finish_report(report):
            try:
                report["database"] = collect_db_stats(db, run_started_at)
            except Exception as error:  # monitoring must never fail the run
                report["database_error"] = f"{type(error).__name__}: {error}"[:300]
            write_step_summary(report)
            publish_ops(store, report)

        try:
            if args.from_artifacts is not None:
                from cloud.parallel import run_from_artifacts

                checkpoint = None
                result = asyncio.run(run_from_artifacts(args.from_artifacts, 250))
            else:
                checkpoint = QueryCheckpoint(db, CONFIG)
                result = asyncio.run(run(CONFIG, 250, checkpoint=checkpoint))
        except Exception as error:
            report = {
                "status": "error",
                "duration_seconds": round(time.monotonic() - started, 1),
                "error": redact(f"{type(error).__name__}: {error}")[:500],
            }
            finish_report(report)
            raise
        report = result.payload()
        stats = result.query_stats
        queries = stats["succeeded"] + stats["failed"] + stats["skipped"]
        print(
            json.dumps(
                {
                    "collection_seconds": round(time.monotonic() - started, 2),
                    "collected_jobs": result.unique_jobs_collected,
                    "unavailable_query_rate": (queries - stats["succeeded"]) / max(queries, 1),
                    "status": result.status,
                }
            ),
            flush=True,
        )
        if result.status == "failed":
            finish_report(report)
            raise RuntimeError("Public job collection failed")
        try:
            if checkpoint is not None:
                checkpoint.clear()
            maintain(db, expire=should_expire(result))
            manifest = publish(db, store)
        except Exception as error:
            report["status"] = "error"
            report["error"] = redact(f"publish: {type(error).__name__}: {error}")[:500]
            finish_report(report)
            raise
        report["feed"] = {
            "version": manifest["version"],
            "snapshot_jobs": manifest["snapshot"]["count"],
        }
        finish_report(report)
        if result.status == "partial":
            raise RuntimeError("Published available jobs; some provider queries failed")


if __name__ == "__main__":
    main()
