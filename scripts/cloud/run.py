"""Run the public JobSpy campaign and publish its versioned R2 feed."""

import asyncio
import gzip
import hashlib
import json
import os
import time
from pathlib import Path

import psycopg

from scripts.cloud.ops_report import publish_ops, write_step_summary
from scripts.cloud.ops_stats import collect_db_stats
from scripts.cloud.publish import publish, r2_from_env
from scripts.collection.run_collection_campaign import run
from wecanfindintern.domain.normalized_job import NormalizedJob

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "scripts/collection/collection_plans.json"


class QueryCheckpoint:
    def __init__(self, db, config):
        self.config_hash = hashlib.sha256(config.read_bytes()).hexdigest()
        self.db = db
        self.completed = {}

    def restore(self, sweep):
        self.campaign = f"{self.config_hash}/{sweep}"
        self.db.execute(
            """
            DELETE FROM feed_checkpoint WHERE campaign <> %s OR saved_at < now()-interval '6 hours'
        """,
            (self.campaign,),
        )
        self.completed.clear()
        for plan, source, records in self.db.execute(
            "SELECT plan, source, records FROM feed_checkpoint"
        ):
            self.completed[plan, source] = [
                NormalizedJob.model_validate(row) for row in json.loads(gzip.decompress(records))
            ]
        print(f"Restored {len(self.completed)} completed queries", flush=True)

    def save(self, key, jobs):
        body = json.dumps([job.model_dump(mode="json") for job in jobs]).encode()
        self.db.execute(
            """
            INSERT INTO feed_checkpoint(plan,source,campaign,records) VALUES (%s,%s,%s,%s)
            ON CONFLICT(plan,source) DO UPDATE SET records=excluded.records,
                saved_at=now(), campaign=excluded.campaign
        """,
            (*key, self.campaign, gzip.compress(body, compresslevel=1)),
        )


def maintain(db, *, expire):
    with db.transaction():
        if expire:
            db.execute("""
                UPDATE jobs SET status=4, closed_at=now()
                WHERE status IN (1,2) AND last_seen_at < now()-interval '14 days'
            """)
        db.execute("""
            DELETE FROM jobs WHERE status IN (3,4) AND closed_at < now()-interval '7 days'
        """)
        # Keep a current LinkedIn detail payload for the existing read-through cache.
        db.execute("""
            DELETE FROM raw_job_snapshots r
            WHERE r.scraped_at < now()-interval '1 day'
              AND NOT EXISTS (
                SELECT FROM job_sources s WHERE s.id=r.job_source_id AND s.source='linkedin'
                  AND r.id=(SELECT id FROM raw_job_snapshots WHERE job_source_id=s.id
                            ORDER BY scraped_at DESC,id DESC LIMIT 1)
              )
        """)
        db.execute("DELETE FROM dedupe_decisions WHERE created_at < now()-interval '7 days'")
        # Successful sweep count is the existing collector's durable full-scan clock.
        # Keep those lightweight counters, and clear obsolete diagnostic text.
        db.execute("""
            UPDATE ingestion_runs SET error_summary=NULL
            WHERE error_summary IS NOT NULL AND started_at < now()-interval '7 days'
        """)


def main():
    # These are workflow-only credentials. Desktop code imports neither boto3 nor this module.
    url = os.environ["DATABASE_URL"]
    store = r2_from_env()
    with psycopg.connect(url, autocommit=True) as db:
        schema, locked = db.execute("""
            SELECT current_schema(), pg_try_advisory_lock(790341567)
        """).fetchone()
        if schema != "job_feed" or not locked:
            raise RuntimeError(
                "Expected the private job_feed schema and an exclusive campaign lock"
            )
        started = time.monotonic()
        run_started_at = db.execute("SELECT now()").fetchone()[0]

        def finish_report(report):
            try:
                report["database"] = collect_db_stats(db, run_started_at)
            except Exception as error:  # monitoring must never fail the run
                report["database_error"] = f"{type(error).__name__}: {error}"[:300]
            write_step_summary(report)
            publish_ops(store, report)

        try:
            result = asyncio.run(run(CONFIG, 250, checkpoint=QueryCheckpoint(db, CONFIG)))
        except Exception as error:
            report = {
                "status": "error",
                "duration_seconds": round(time.monotonic() - started, 1),
                "error": f"{type(error).__name__}: {error}"[:500],
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
        db.execute("DELETE FROM feed_checkpoint")
        maintain(db, expire=result.status == "success")
        manifest = publish(db, store)
        report["feed"] = {
            "version": manifest["version"],
            "snapshot_jobs": manifest["snapshot"]["count"],
        }
        finish_report(report)
        if result.status == "partial":
            raise RuntimeError("Published available jobs; some provider queries failed")


if __name__ == "__main__":
    main()
