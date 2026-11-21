"""Durable native pages and LinkedIn detail outcomes for one collection sweep."""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from wecanfindintern.deduplication import record_completeness
from wecanfindintern.domain.normalized_job import NormalizedJob


def keep_best(records: dict[str, NormalizedJob], job: NormalizedJob) -> None:
    previous = records.get(job.source_fingerprint)
    if previous is None or record_completeness(job.raw) > record_completeness(previous.raw):
        records[job.source_fingerprint] = job


@dataclass
class QueryProgress:
    cursor: str | None = None
    last_cursor: str | None = None
    complete: bool = False
    sequence: int = 0
    visited: set[str] = field(default_factory=set)
    jobs: dict[str, NormalizedJob] = field(default_factory=dict)

    def advance(self, jobs, next_cursor, restart=False):
        if restart:
            self.visited.clear()
        elif self.cursor is not None:
            self.visited.add(self.cursor)
        self.sequence += 1
        self.last_cursor = self.cursor
        self.cursor = next_cursor
        self.complete = next_cursor is None and not restart
        for job in jobs:
            keep_best(self.jobs, job)


class QueryCheckpoint:
    """One compressed row per page or resolved detail, using the existing cloud table."""

    def __init__(self, db, config: Path):
        self.db = db
        config_data = json.dumps(
            json.loads(config.read_bytes()), sort_keys=True, separators=(",", ":")
        )
        self.config_hash = hashlib.sha256(config_data.encode()).hexdigest()
        self.queries: dict[tuple[str, str], QueryProgress] = {}
        # A stored None is an explicit 404/410, never a transient detail failure.
        self.details: dict[str, NormalizedJob | None] = {}
        self.observed: dict[str, datetime] = {}
        self.lock = Lock()

    @classmethod
    def local(cls, directory: Path, config: Path, database_url: str):
        directory.mkdir(parents=True, exist_ok=True)
        identity = hashlib.sha256(f"{config.resolve()}:{database_url}".encode()).hexdigest()[:20]
        db = sqlite3.connect(
            directory / f"collection-{identity}.sqlite3",
            isolation_level=None,
            check_same_thread=False,
        )
        db.execute("""
            CREATE TABLE IF NOT EXISTS feed_checkpoint (
                plan TEXT NOT NULL, source TEXT NOT NULL, campaign TEXT NOT NULL,
                records BLOB NOT NULL, saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(plan,source)
            )
        """)
        return cls(db, config)

    def _execute(self, sql, params=()):
        if isinstance(self.db, sqlite3.Connection):
            sql = sql.replace("%s", "?")
        return self.db.execute(sql, params)

    def restore(self, sweep):
        # Legacy checkpoints ended at a row quota and cannot certify exhaustion.
        self.campaign = f"pages-v1/{self.config_hash}/{sweep}"
        self.queries.clear()
        self.details.clear()
        self.observed.clear()
        self.attempt_at = datetime.now(UTC)
        self.started_at = self.attempt_at
        self.retry_at = 0.0
        self._execute("DELETE FROM feed_checkpoint WHERE campaign <> %s", (self.campaign,))
        rows = self._execute(
            "SELECT plan, source, records FROM feed_checkpoint "
            "WHERE campaign=%s ORDER BY plan,source",
            (self.campaign,),
        )
        has_clock = False
        for key, source, body in rows:
            data = json.loads(gzip.decompress(body))
            if key == "clock":
                self.started_at = datetime.fromisoformat(data["started_at"])
                self.retry_at = data["retry_at"]
                has_clock = True
            elif key.startswith("detail/"):
                self.details[key[7:]] = NormalizedJob.model_validate(data) if data else None
            else:
                state = self.queries.setdefault((data["query"], source), QueryProgress())
                jobs = [NormalizedJob.model_validate(row) for row in data["jobs"]]
                state.advance(jobs, data["next_cursor"], data["restart"])
                observed_at = datetime.fromisoformat(
                    data.get("observed_at", self.started_at.isoformat())
                )
                for job in jobs:
                    fp = job.source_fingerprint
                    self.observed[fp] = max(observed_at, self.observed.get(fp, observed_at))
        if not has_clock:
            self.save_cooldown(0.0)

    def _put(self, plan, source, data):
        blob = gzip.compress(json.dumps(data, separators=(",", ":")).encode(), compresslevel=1)
        with self.lock:
            self._execute(
                """
                INSERT INTO feed_checkpoint(plan,source,campaign,records) VALUES (%s,%s,%s,%s)
                ON CONFLICT(plan,source) DO UPDATE SET campaign=excluded.campaign,
                    records=excluded.records, saved_at=CURRENT_TIMESTAMP
            """,
                (plan, source, self.campaign, blob),
            )

    def save_page(self, key, sequence, jobs, next_cursor, restart):
        name, source = key
        self._put(
            f"page/{name}/{sequence:012d}",
            source,
            {
                "query": name,
                "observed_at": self.attempt_at.isoformat(),
                "jobs": [job.model_dump(mode="json") for job in jobs],
                "next_cursor": next_cursor,
                "restart": restart,
            },
        )

        for job in jobs:
            self.observed[job.source_fingerprint] = self.attempt_at

    def save_detail(self, fingerprint, job):
        self._put(f"detail/{fingerprint}", "linkedin", job.model_dump(mode="json") if job else None)

    def save_cooldown(self, until):
        self._put(
            "clock", "linkedin", {"started_at": self.started_at.isoformat(), "retry_at": until}
        )
        self.retry_at = until

    def clear(self):
        self._execute("DELETE FROM feed_checkpoint WHERE campaign=%s", (self.campaign,))
