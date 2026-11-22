"""Entry point: run one campaign, then maintain the database and publish the R2 feed."""

import asyncio
import json
import os
import time

import psycopg

from cloud.campaign import run
from cloud.config import DEFAULT_PLAN_PATH
from cloud.ops.report import publish_ops, redact, write_step_summary
from cloud.ops.stats import collect_db_stats
from cloud.publish.feed import publish, r2_from_env
from cloud.storage.checkpoint import QueryCheckpoint
from cloud.storage.maintenance import acquire_campaign_lock, maintain

CONFIG = DEFAULT_PLAN_PATH


def main():
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
            checkpoint.clear()
            maintain(db, expire=result.status == "success")
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
