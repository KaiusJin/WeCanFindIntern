"""Dedicated SQLite storage for WaterlooWorks postings and collection runs."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from wecanfindintern.waterlooworks.extractor import normalize_waterlooworks_job

SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS waterlooworks_runs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    unique_job_count INTEGER NOT NULL DEFAULT 0,
    posting_success_count INTEGER NOT NULL DEFAULT 0,
    posting_failed_count INTEGER NOT NULL DEFAULT 0,
    board_failed_count INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT
);

CREATE TABLE IF NOT EXISTS waterlooworks_board_runs (
    run_id TEXT NOT NULL REFERENCES waterlooworks_runs(id) ON DELETE CASCADE,
    board TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    discovered_count INTEGER NOT NULL DEFAULT 0,
    posting_success_count INTEGER NOT NULL DEFAULT 0,
    posting_failed_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    PRIMARY KEY (run_id, board)
);

CREATE TABLE IF NOT EXISTS waterlooworks_jobs (
    source_job_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    organization TEXT,
    division TEXT,
    location_text TEXT,
    city TEXT,
    province TEXT,
    country TEXT,
    work_mode TEXT NOT NULL DEFAULT 'unknown',
    date_posted TEXT,
    application_deadline TEXT,
    application_url TEXT,
    application_delivery TEXT,
    application_documents TEXT,
    source_url TEXT NOT NULL,
    description TEXT,
    raw_payload TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS waterlooworks_job_boards (
    source_job_id TEXT NOT NULL REFERENCES waterlooworks_jobs(source_job_id) ON DELETE CASCADE,
    board TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (source_job_id, board)
);

CREATE INDEX IF NOT EXISTS idx_ww_jobs_last_seen
    ON waterlooworks_jobs(last_seen_at DESC, source_job_id DESC);
CREATE INDEX IF NOT EXISTS idx_ww_jobs_organization
    ON waterlooworks_jobs(organization, title);
CREATE INDEX IF NOT EXISTS idx_ww_job_boards_board
    ON waterlooworks_job_boards(board, source_job_id);
CREATE INDEX IF NOT EXISTS idx_ww_board_runs_run
    ON waterlooworks_board_runs(run_id, board);
"""


class WaterlooWorksRepository:
    """Synchronous SQLite repository; callers run methods in a worker thread."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            self._drop_legacy_content_tracking_columns(connection)

    def start_run(self, boards: list[dict[str, Any]]) -> str:
        run_id = str(uuid4())
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO waterlooworks_runs(id, status, started_at) "
                "VALUES (?, 'collecting', ?)",
                (run_id, now),
            )
            connection.executemany(
                """
                INSERT INTO waterlooworks_board_runs(run_id, board, status)
                VALUES (?, ?, 'pending')
                """,
                [(run_id, board["name"]) for board in boards],
            )
        return run_id

    def mark_board_collecting(self, run_id: str, board: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE waterlooworks_board_runs
                SET status='collecting', started_at=?, error=NULL
                WHERE run_id=? AND board=?
                """,
                (_now(), run_id, board),
            )

    def mark_board_failed(self, run_id: str, board: str, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE waterlooworks_board_runs
                SET status='failed', finished_at=?, error=?
                WHERE run_id=? AND board=?
                """,
                (_now(), error[:1000], run_id, board),
            )

    def store_board_postings(
        self,
        run_id: str,
        board: str,
        raw_jobs: list[dict[str, Any]],
    ) -> tuple[Counter[str], list[str]]:
        outcomes: Counter[str] = Counter()
        errors: list[str] = []
        now = _now()
        with self._connect() as connection:
            for raw in raw_jobs:
                if not isinstance(raw, dict):
                    outcomes["posting_failed"] += 1
                    errors.append("unknown: invalid posting payload")
                    continue
                if raw.get("error"):
                    outcomes["posting_failed"] += 1
                    errors.append(f"{raw.get('id', 'unknown')}: {raw['error']}")
                    continue
                source_job_id = _text(raw.get("id"))
                if not source_job_id:
                    outcomes["posting_failed"] += 1
                    errors.append("unknown: WaterlooWorks posting has no Job ID")
                    continue
                existing = connection.execute(
                    "SELECT 1 FROM waterlooworks_jobs WHERE source_job_id=?",
                    (source_job_id,),
                ).fetchone()
                try:
                    if existing is None:
                        record = _storage_record(raw)
                        connection.execute(
                            """
                            INSERT INTO waterlooworks_jobs(
                                source_job_id, title, organization, division, location_text,
                                city, province, country, work_mode, date_posted,
                                application_deadline, application_url, application_delivery,
                                application_documents, source_url, description, raw_payload,
                                first_seen_at, last_seen_at
                            ) VALUES (
                                :source_job_id, :title, :organization, :division, :location_text,
                                :city, :province, :country, :work_mode, :date_posted,
                                :application_deadline, :application_url, :application_delivery,
                                :application_documents, :source_url, :description, :raw_payload,
                                :first_seen_at, :last_seen_at
                            )
                            """,
                            record,
                        )
                    else:
                        connection.execute(
                            "UPDATE waterlooworks_jobs SET last_seen_at=? WHERE source_job_id=?",
                            (now, source_job_id),
                        )
                except (KeyError, TypeError, ValueError) as error:
                    outcomes["posting_failed"] += 1
                    errors.append(f"{raw.get('id', 'unknown')}: {error}")
                    continue
                outcomes["posting_success"] += 1
                connection.execute(
                    """
                    INSERT INTO waterlooworks_job_boards(
                        source_job_id, board, first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(source_job_id, board)
                    DO UPDATE SET last_seen_at=excluded.last_seen_at
                    """,
                    (source_job_id, board, now, now),
                )

            connection.execute(
                """
                UPDATE waterlooworks_board_runs SET
                    status='completed', finished_at=?, discovered_count=?,
                    posting_success_count=?, posting_failed_count=?, error=?
                WHERE run_id=? AND board=?
                """,
                (
                    now,
                    len(raw_jobs),
                    outcomes["posting_success"],
                    outcomes["posting_failed"],
                    "; ".join(errors[:5])[:1000] or None,
                    run_id,
                    board,
                ),
            )
        return outcomes, errors

    def finish_run(self, run_id: str, error_summary: str | None = None) -> dict[str, Any]:
        with self._connect() as connection:
            totals = connection.execute(
                """
                SELECT
                    COALESCE(SUM(posting_success_count), 0) posting_success_count,
                    COALESCE(SUM(posting_failed_count), 0) posting_failed_count,
                    COALESCE(SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END), 0)
                        board_failed_count
                FROM waterlooworks_board_runs WHERE run_id=?
                """,
                (run_id,),
            ).fetchone()
            unique_jobs = connection.execute(
                "SELECT COUNT(*) count FROM waterlooworks_jobs"
            ).fetchone()["count"]
            is_partial = totals["board_failed_count"] or totals["posting_failed_count"]
            status = "partial" if is_partial else "completed"
            connection.execute(
                """
                UPDATE waterlooworks_runs SET
                    status=?, finished_at=?, unique_job_count=?,
                    posting_success_count=?, posting_failed_count=?, board_failed_count=?,
                    error_summary=?
                WHERE id=?
                """,
                (
                    status,
                    _now(),
                    unique_jobs,
                    totals["posting_success_count"],
                    totals["posting_failed_count"],
                    totals["board_failed_count"],
                    error_summary,
                    run_id,
                ),
            )
        return dict(totals) | {"unique_job_count": unique_jobs, "status": status}

    def fail_run(self, run_id: str, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE waterlooworks_runs
                SET status='failed', finished_at=?, error_summary=? WHERE id=?
                """,
                (_now(), error[:1000], run_id),
            )

    def list_jobs(
        self,
        *,
        board: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        predicates: list[str] = []
        params: list[Any] = []
        if board:
            predicates.append(
                "EXISTS (SELECT 1 FROM waterlooworks_job_boards b "
                "WHERE b.source_job_id=j.source_job_id AND b.board=?)"
            )
            params.append(board)
        if query:
            predicates.append("(j.title LIKE ? OR j.organization LIKE ? OR j.description LIKE ?)")
            term = f"%{query}%"
            params.extend([term, term, term])
        where = f"WHERE {' AND '.join(predicates)}" if predicates else ""
        with self._connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) count FROM waterlooworks_jobs j {where}",
                params,
            ).fetchone()["count"]
            rows = connection.execute(
                f"""
                SELECT j.*,
                    (SELECT json_group_array(board) FROM waterlooworks_job_boards b
                     WHERE b.source_job_id=j.source_job_id) boards
                FROM waterlooworks_jobs j {where}
                ORDER BY j.last_seen_at DESC, j.source_job_id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["boards"] = json.loads(item["boards"] or "[]")
            item.pop("raw_payload", None)
            item.pop("payload_hash", None)
            item.pop("description", None)
            items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def count_source_ids(self, source_job_ids: list[str]) -> int:
        if not source_job_ids:
            return 0
        placeholders = ",".join("?" for _ in source_job_ids)
        with self._connect() as connection:
            return connection.execute(
                f"SELECT COUNT(*) count FROM waterlooworks_jobs "
                f"WHERE source_job_id IN ({placeholders})",
                source_job_ids,
            ).fetchone()["count"]

    def latest_run(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            run = connection.execute(
                "SELECT * FROM waterlooworks_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if run is None:
                return None
            boards = connection.execute(
                "SELECT * FROM waterlooworks_board_runs WHERE run_id=? ORDER BY rowid",
                (run["id"],),
            ).fetchall()
        return dict(run) | {"boards": [dict(board) for board in boards]}

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @staticmethod
    def _drop_legacy_content_tracking_columns(connection: sqlite3.Connection) -> None:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(waterlooworks_jobs)").fetchall()
        }
        for column in ("payload_hash", "updated_at"):
            if column in columns:
                connection.execute(f"ALTER TABLE waterlooworks_jobs DROP COLUMN {column}")


def _storage_record(raw: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_waterlooworks_job(raw)
    location = raw.get("location") or {}
    application = raw.get("application") or {}
    payload_json = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    now = _now()
    return {
        "source_job_id": normalized.source_job_id,
        "title": normalized.title,
        "organization": normalized.company_name,
        "division": normalized.job_function,
        "location_text": normalized.location_text,
        "city": _text(location.get("city")),
        "province": _text(location.get("province")),
        "country": _text(location.get("country")),
        "work_mode": "remote" if normalized.is_remote else "unknown",
        "date_posted": normalized.date_posted.isoformat() if normalized.date_posted else None,
        "application_deadline": _text(application.get("deadline")),
        "application_url": normalized.direct_url,
        "application_delivery": _text(application.get("delivery")),
        "application_documents": _text(application.get("documentsRequired")),
        "source_url": normalized.source_url,
        "description": normalized.description,
        "raw_payload": payload_json,
        "first_seen_at": now,
        "last_seen_at": now,
    }


def _text(value: Any) -> str | None:
    text = " ".join(str(value or "").split())
    return text or None


def _now() -> str:
    return datetime.now(UTC).isoformat()
