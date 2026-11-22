"""Per-query checkpoint so a retried workflow run skips queries it already finished."""

import gzip
import hashlib
import json

from wecanfindintern.domain.normalized_job import NormalizedJob


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

    def clear(self):
        self.db.execute("DELETE FROM feed_checkpoint")
