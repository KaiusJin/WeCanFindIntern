"""Run the public JobSpy campaign and publish its versioned R2 feed."""

import asyncio
import gzip
import hashlib
import json
import os
import time
from pathlib import Path

import psycopg

from scripts.cloud.publish import R2, publish
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
    import boto3

    # These are workflow-only credentials. Desktop code imports neither boto3 nor this module.
    url = os.environ["DATABASE_URL"]
    store = R2(
        boto3.client(
            "s3",
            endpoint_url=os.environ["R2_ENDPOINT_URL"],
            region_name="auto",
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        ),
        os.environ["R2_BUCKET"],
    )
    with psycopg.connect(url, autocommit=True) as db:
        schema, locked = db.execute("""
            SELECT current_schema(), pg_try_advisory_lock(790341567)
        """).fetchone()
        if schema != "job_feed" or not locked:
            raise RuntimeError(
                "Expected the private job_feed schema and an exclusive campaign lock"
            )
        started = time.monotonic()
        result = asyncio.run(run(CONFIG, 250, checkpoint=QueryCheckpoint(db, CONFIG)))
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
            raise RuntimeError("Public job collection failed")
        db.execute("DELETE FROM feed_checkpoint")
        maintain(db, expire=result.status == "success")
        publish(db, store)
        if result.status == "partial":
            raise RuntimeError("Published available jobs; some provider queries failed")


if __name__ == "__main__":
    main()
