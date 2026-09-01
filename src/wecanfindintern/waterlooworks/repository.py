"""Dedicated SQLite storage for WaterlooWorks postings and collection runs."""

from __future__ import annotations

import base64
import binascii
import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from wecanfindintern.domain.classification import normalize_tag
from wecanfindintern.domain.jobs import canonical_job_from_normalized
from wecanfindintern.domain.location import (
    CANADIAN_REGION_NAMES,
    COUNTRY_ALIASES,
    COUNTRY_NAMES,
    US_REGION_NAMES,
    normalize_region_code,
)
from wecanfindintern.waterlooworks.extractor import (
    normalize_waterlooworks_job,
    waterlooworks_salary,
)
from wecanfindintern.waterlooworks.records import decode_waterlooworks_job
from wecanfindintern.waterlooworks.taxonomy import (
    WATERLOOWORKS_BOARD_OPPORTUNITY_TYPES,
    boards_for_opportunity_types,
    infer_waterloo_opportunity_type,
)
from wecanfindintern.waterlooworks.text import optional_waterlooworks_text

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
    salary_min TEXT,
    salary_max TEXT,
    salary_interval TEXT,
    salary_currency TEXT,
    date_posted TEXT,
    application_deadline TEXT,
    application_url TEXT,
    application_delivery TEXT,
    application_documents TEXT,
    source_url TEXT NOT NULL,
    description TEXT,
    skill_tags TEXT NOT NULL DEFAULT '[]',
    opportunity_type TEXT,
    schedule_types TEXT NOT NULL DEFAULT '[]',
    primary_schedule_type TEXT,
    job_category TEXT,
    job_subcategories TEXT NOT NULL DEFAULT '[]',
    requirement_tags TEXT NOT NULL DEFAULT '[]',
    display_tags TEXT NOT NULL DEFAULT '[]',
    classification_version INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS waterlooworks_applications (
    source_job_id TEXT PRIMARY KEY
        REFERENCES waterlooworks_jobs(source_job_id) ON DELETE CASCADE,
    term TEXT,
    app_status TEXT NOT NULL,
    job_status TEXT,
    openings TEXT,
    application_deadline TEXT,
    submitted_at TEXT,
    submitted_by TEXT,
    raw_payload TEXT NOT NULL,
    synced_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ww_jobs_last_seen
    ON waterlooworks_jobs(last_seen_at DESC, source_job_id DESC);
CREATE INDEX IF NOT EXISTS idx_ww_jobs_organization
    ON waterlooworks_jobs(organization, title);
CREATE INDEX IF NOT EXISTS idx_ww_job_boards_board
    ON waterlooworks_job_boards(board, source_job_id);
CREATE INDEX IF NOT EXISTS idx_ww_board_runs_run
    ON waterlooworks_board_runs(run_id, board);
CREATE INDEX IF NOT EXISTS idx_ww_applications_status
    ON waterlooworks_applications(app_status, submitted_at DESC);
"""

INSERT_WATERLOOWORKS_JOB_SQL = """
    INSERT INTO waterlooworks_jobs(
        source_job_id, title, organization, division, location_text,
        city, province, country, work_mode,
        salary_min, salary_max, salary_interval, salary_currency,
        date_posted, application_deadline, application_url,
        application_delivery, application_documents, source_url,
        description, skill_tags, opportunity_type, schedule_types,
        primary_schedule_type, job_category, job_subcategories,
        requirement_tags, display_tags, classification_version,
        raw_payload, first_seen_at, last_seen_at
    ) VALUES (
        :source_job_id, :title, :organization, :division, :location_text,
        :city, :province, :country, :work_mode,
        :salary_min, :salary_max, :salary_interval, :salary_currency,
        :date_posted, :application_deadline, :application_url,
        :application_delivery, :application_documents, :source_url,
        :description, :skill_tags, :opportunity_type, :schedule_types,
        :primary_schedule_type, :job_category, :job_subcategories,
        :requirement_tags, :display_tags, :classification_version,
        :raw_payload, :first_seen_at, :last_seen_at
    )
"""


class WaterlooWorksRepository:
    """Synchronous SQLite repository; callers run methods in a worker thread."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            self._ensure_salary_columns(connection)
            self._ensure_skill_columns(connection)
            self._ensure_classification_columns(connection)

    @staticmethod
    def _insert_job_record(
        connection: sqlite3.Connection, record: dict[str, Any]
    ) -> None:
        connection.execute(INSERT_WATERLOOWORKS_JOB_SQL, record)

    @staticmethod
    def _insert_board_membership(
        connection: sqlite3.Connection,
        *,
        source_job_id: str,
        board: str,
        seen_at: str,
    ) -> None:
        # Job content is immutable after first sighting, while relationship
        # freshness records every crawl that still observes this membership.
        connection.execute(
            """
            INSERT INTO waterlooworks_job_boards(
                source_job_id, board, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(source_job_id, board) DO UPDATE SET
                last_seen_at=excluded.last_seen_at
            """,
            (source_job_id, board, seen_at, seen_at),
        )

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
                if existing is not None:
                    connection.execute(
                        "UPDATE waterlooworks_jobs SET last_seen_at=? WHERE source_job_id=?",
                        (now, source_job_id),
                    )
                    self._insert_board_membership(
                        connection,
                        source_job_id=source_job_id,
                        board=board,
                        seen_at=now,
                    )
                    # WaterlooWorks Job IDs are immutable in the local corpus. A later
                    # crawl may add a missing board edge but never rewrites job content.
                    outcomes["posting_known"] += 1
                    continue
                try:
                    record = _storage_record(raw)
                    self._insert_job_record(connection, record)
                except (KeyError, TypeError, ValueError) as error:
                    outcomes["posting_failed"] += 1
                    errors.append(f"{raw.get('id', 'unknown')}: {error}")
                    continue
                outcomes["posting_inserted"] += 1
                self._insert_board_membership(
                    connection,
                    source_job_id=source_job_id,
                    board=board,
                    seen_at=now,
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
                    outcomes["posting_inserted"],
                    outcomes["posting_failed"],
                    "; ".join(errors[:5])[:1000] or None,
                    run_id,
                    board,
                ),
            )
        return outcomes, errors

    def store_applications(
        self, raw_applications: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, int], list[str]]:
        """Upsert submitted applications and create missing WaterlooWorks jobs."""

        stored: list[dict[str, Any]] = []
        counts = {"stored": 0, "new_jobs": 0, "detail_failures": 0, "failed": 0}
        errors: list[str] = []
        now = _now()
        with self._connect() as connection:
            for raw in raw_applications:
                source_job_id = _text(raw.get("id"))
                title = _text(raw.get("title"))
                application = raw.get("applicationRecord") or {}
                app_status = _text(application.get("appStatus"))
                if not source_job_id or not title or not app_status:
                    counts["failed"] += 1
                    errors.append(f"{source_job_id or 'unknown'}: missing Job ID, title, or status")
                    continue
                try:
                    existing = connection.execute(
                        "SELECT 1 FROM waterlooworks_jobs WHERE source_job_id=?",
                        (source_job_id,),
                    ).fetchone()
                    if existing is None:
                        record = _storage_record(raw)
                        self._insert_job_record(connection, record)
                        counts["new_jobs"] += 1
                    else:
                        # The posting snapshot is immutable by Job ID, while this
                        # observation timestamp records that the identity is current.
                        connection.execute(
                            "UPDATE waterlooworks_jobs SET last_seen_at=? "
                            "WHERE source_job_id=?",
                            (now, source_job_id),
                        )
                    connection.execute(
                        """
                        INSERT INTO waterlooworks_applications(
                            source_job_id, term, app_status, job_status, openings,
                            application_deadline, submitted_at, submitted_by,
                            raw_payload, synced_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(source_job_id) DO UPDATE SET
                            term=excluded.term,
                            app_status=excluded.app_status,
                            job_status=excluded.job_status,
                            openings=excluded.openings,
                            application_deadline=excluded.application_deadline,
                            submitted_at=excluded.submitted_at,
                            submitted_by=excluded.submitted_by,
                            raw_payload=excluded.raw_payload,
                            synced_at=excluded.synced_at
                        """,
                        (
                            source_job_id,
                            _text(application.get("term")),
                            app_status,
                            _text(application.get("jobStatus")),
                            _text(application.get("openings")),
                            _text(application.get("applicationDeadline")),
                            _text(application.get("submittedAt")),
                            _text(application.get("submittedBy")),
                            json.dumps(raw, ensure_ascii=False, sort_keys=True),
                            now,
                        ),
                    )
                    if raw.get("detailError"):
                        counts["detail_failures"] += 1
                    counts["stored"] += 1
                    job = self._application_sync_record(connection, source_job_id)
                    if job:
                        stored.append(job)
                except (KeyError, TypeError, ValueError, sqlite3.Error) as error:
                    counts["failed"] += 1
                    errors.append(f"{source_job_id}: {error}")
        return stored, counts, errors

    @staticmethod
    def _application_sync_record(
        connection: sqlite3.Connection, source_job_id: str
    ) -> dict[str, Any] | None:
        row = connection.execute(
            """
            SELECT j.*,
                   a.term AS application_term,
                   a.app_status AS application_status,
                   a.job_status AS application_job_status,
                   a.openings AS application_openings,
                   a.submitted_at AS application_submitted_at,
                   a.submitted_by AS application_submitted_by,
                   a.application_deadline AS submitted_application_deadline
            FROM waterlooworks_jobs j
            JOIN waterlooworks_applications a USING (source_job_id)
            WHERE j.source_job_id=?
            """,
            (source_job_id,),
        ).fetchone()
        return decode_waterlooworks_job(row) if row else None

    def count_applications(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) count FROM waterlooworks_applications"
            ).fetchone()
        return int(row["count"])

    def count_jobs(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) count FROM waterlooworks_jobs"
            ).fetchone()
        return int(row["count"])

    def finish_run(self, run_id: str, error_summary: str | None = None) -> dict[str, Any]:
        with self._connect() as connection:
            totals = connection.execute(
                """
                SELECT
                    COALESCE(SUM(posting_success_count), 0) posting_inserted_count,
                    COALESCE(SUM(max(
                        discovered_count - posting_success_count - posting_failed_count,
                        0
                    )), 0) posting_known_count,
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
                    totals["posting_inserted_count"],
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
        company: str | None = None,
        skill: str | None = None,
        category: str | None = None,
        city: str | None = None,
        region: str | None = None,
        country: str | None = None,
        work_modes: list[str] | None = None,
        opportunity_types: list[str] | None = None,
        posted_after: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        include_description: bool = False,
    ) -> dict[str, Any]:
        predicates: list[str] = []
        params: list[Any] = []
        if board == "applications":
            predicates.append(
                "EXISTS (SELECT 1 FROM waterlooworks_applications a "
                "WHERE a.source_job_id=j.source_job_id)"
            )
        elif board:
            predicates.append(
                "EXISTS (SELECT 1 FROM waterlooworks_job_boards b "
                "WHERE b.source_job_id=j.source_job_id AND b.board=?)"
            )
            params.append(board)
        if query:
            predicates.append("(j.title LIKE ? OR j.organization LIKE ? OR j.description LIKE ?)")
            term = f"%{query}%"
            params.extend([term, term, term])
        if company:
            predicates.append("lower(j.organization) LIKE ?")
            params.append(f"%{company.lower()}%")
        if skill:
            predicates.append(
                "EXISTS (SELECT 1 FROM json_each(j.skill_tags) skill "
                "WHERE lower(skill.value)=?)"
            )
            params.append(normalize_tag(skill))
        if category:
            predicates.append("lower(j.job_category) = ?")
            params.append(normalize_tag(category))
        if city:
            predicates.append("lower(j.city) = ?")
            params.append(city.lower())
        if region:
            country_code = COUNTRY_ALIASES.get((country or "").lower())
            region_code = normalize_region_code(region, country_code)
            region_values = {region.lower()}
            if region_code:
                region_values.add(region_code.lower())
                region_name = (
                    CANADIAN_REGION_NAMES.get(region_code)
                    if country_code == "CA"
                    else US_REGION_NAMES.get(region_code)
                )
                if region_name:
                    region_values.add(region_name.lower())
            placeholders = ",".join("?" for _ in region_values)
            predicates.append(f"lower(j.province) IN ({placeholders})")
            params.extend(sorted(region_values))
        if country:
            country_code = COUNTRY_ALIASES.get(country.lower(), country.upper())
            country_values = {
                alias for alias, code in COUNTRY_ALIASES.items() if code == country_code
            }
            country_values.add(country_code.lower())
            if country_code in COUNTRY_NAMES:
                country_values.add(COUNTRY_NAMES[country_code].lower())
            placeholders = ",".join("?" for _ in country_values)
            predicates.append(f"lower(j.country) IN ({placeholders})")
            params.extend(sorted(country_values))
        if work_modes:
            placeholders = ",".join("?" for _ in work_modes)
            predicates.append(f"j.work_mode IN ({placeholders})")
            params.extend(work_modes)
        if posted_after:
            predicates.append("j.date_posted >= ?")
            params.append(posted_after)
        if opportunity_types:
            normalized_types = [
                value.strip().lower() for value in opportunity_types if value.strip()
            ] or ["__invalid_opportunity_type__"]
            type_placeholders = ",".join("?" for _ in normalized_types)
            mapped_boards = boards_for_opportunity_types(normalized_types)
            taxonomy_boards = sorted(WATERLOOWORKS_BOARD_OPPORTUNITY_TYPES)
            taxonomy_placeholders = ",".join("?" for _ in taxonomy_boards)
            if mapped_boards:
                board_placeholders = ",".join("?" for _ in mapped_boards)
                predicates.append(
                    f"((j.opportunity_type IN ({type_placeholders}) AND NOT EXISTS ("
                    "SELECT 1 FROM waterlooworks_job_boards b "
                    "WHERE b.source_job_id=j.source_job_id "
                    f"AND b.board IN ({taxonomy_placeholders}))) OR EXISTS ("
                    "SELECT 1 FROM waterlooworks_job_boards b "
                    "WHERE b.source_job_id=j.source_job_id "
                    f"AND b.board IN ({board_placeholders})))"
                )
                params.extend([*normalized_types, *taxonomy_boards, *mapped_boards])
            else:
                predicates.append(
                    f"(j.opportunity_type IN ({type_placeholders}) AND NOT EXISTS ("
                    "SELECT 1 FROM waterlooworks_job_boards b "
                    "WHERE b.source_job_id=j.source_job_id "
                    f"AND b.board IN ({taxonomy_placeholders})))"
                )
                params.extend([*normalized_types, *taxonomy_boards])
        where = f"WHERE {' AND '.join(predicates)}" if predicates else ""
        with self._connect() as connection:
            summary = connection.execute(
                f"""
                SELECT COUNT(*) count, MAX(j.last_seen_at) last_updated_at
                FROM waterlooworks_jobs j {where}
                """,
                params,
            ).fetchone()
            select_predicates = list(predicates)
            select_params = list(params)
            if cursor:
                select_predicates.append("j.rowid < ?")
                select_params.append(_decode_cursor(cursor))
            select_where = (
                f"WHERE {' AND '.join(select_predicates)}" if select_predicates else ""
            )
            rows = connection.execute(
                f"""
                SELECT j.*, j.rowid AS pagination_rowid,
                    a.app_status AS application_status,
                    a.submitted_at AS application_submitted_at,
                    a.term AS application_term,
                    a.job_status AS application_job_status,
                    a.openings AS application_openings,
                    a.submitted_by AS application_submitted_by,
                    a.application_deadline AS submitted_application_deadline,
                    (SELECT json_group_array(board) FROM (
                        SELECT board FROM waterlooworks_job_boards b
                        WHERE b.source_job_id=j.source_job_id
                        ORDER BY board
                    )) boards
                FROM waterlooworks_jobs j
                LEFT JOIN waterlooworks_applications a USING (source_job_id)
                {select_where}
                ORDER BY j.rowid DESC
                LIMIT ?
                """,
                [*select_params, limit + 1],
            ).fetchall()
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        items = [
            decode_waterlooworks_job(
                row,
                include_description=include_description,
            )
            for row in page_rows
        ]
        next_cursor = (
            _encode_cursor(int(page_rows[-1]["pagination_rowid"]))
            if has_more and page_rows
            else None
        )
        return {
            "schema_version": "waterlooworks-job-page.v1",
            "items": items,
            "total_count": int(summary["count"]),
            "last_updated_at": summary["last_updated_at"],
            "next_cursor": next_cursor,
            "has_more": has_more,
        }

    def get_job(self, source_job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT j.*, a.app_status AS application_status,
                       a.submitted_at AS application_submitted_at,
                       a.term AS application_term,
                       a.job_status AS application_job_status,
                       a.openings AS application_openings,
                       a.submitted_by AS application_submitted_by,
                       a.application_deadline AS submitted_application_deadline,
                       (SELECT json_group_array(board) FROM (
                            SELECT board FROM waterlooworks_job_boards b
                            WHERE b.source_job_id=j.source_job_id
                            ORDER BY board
                       )) AS boards
                FROM waterlooworks_jobs j
                LEFT JOIN waterlooworks_applications a USING (source_job_id)
                WHERE j.source_job_id=?
                """,
                (source_job_id,),
            ).fetchone()
        if row is None:
            return None
        return decode_waterlooworks_job(row)

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

    def latest_application_sync_at(self) -> str | None:
        """Return the most recent submitted-application import timestamp."""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT MAX(synced_at) AS synced_at FROM waterlooworks_applications"
            ).fetchone()
        return row["synced_at"] if row and row["synced_at"] else None

    def latest_job_update_at(self) -> str | None:
        """Return the most recent timestamp at which a stored job was seen."""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT MAX(last_seen_at) AS last_seen_at FROM waterlooworks_jobs"
            ).fetchone()
        return row["last_seen_at"] if row and row["last_seen_at"] else None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @staticmethod
    def _ensure_salary_columns(connection: sqlite3.Connection) -> None:
        """Add structured salary columns without rewriting existing job records."""

        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(waterlooworks_jobs)").fetchall()
        }
        for column in ("salary_min", "salary_max", "salary_interval", "salary_currency"):
            if column not in columns:
                connection.execute(f"ALTER TABLE waterlooworks_jobs ADD COLUMN {column} TEXT")

    @staticmethod
    def _ensure_skill_columns(connection: sqlite3.Connection) -> None:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(waterlooworks_jobs)").fetchall()
        }
        if "skill_tags" not in columns:
            connection.execute(
                "ALTER TABLE waterlooworks_jobs ADD COLUMN skill_tags TEXT NOT NULL DEFAULT '[]'"
            )

    @staticmethod
    def _ensure_classification_columns(connection: sqlite3.Connection) -> None:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(waterlooworks_jobs)").fetchall()
        }
        definitions = {
            "opportunity_type": "TEXT",
            "schedule_types": "TEXT NOT NULL DEFAULT '[]'",
            "primary_schedule_type": "TEXT",
            "job_category": "TEXT",
            "job_subcategories": "TEXT NOT NULL DEFAULT '[]'",
            "requirement_tags": "TEXT NOT NULL DEFAULT '[]'",
            "display_tags": "TEXT NOT NULL DEFAULT '[]'",
            "classification_version": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, definition in definitions.items():
            if column not in columns:
                connection.execute(
                    f"ALTER TABLE waterlooworks_jobs ADD COLUMN {column} {definition}"
                )


def _storage_record(raw: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_waterlooworks_job(raw)
    canonical = canonical_job_from_normalized(normalized)
    salary = waterlooworks_salary(raw)
    application = raw.get("application") or {}
    payload_json = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    now = _now()
    opportunity_type = infer_waterloo_opportunity_type(
        [optional_waterlooworks_text(raw.get("jobBoard")) or ""]
    ) or canonical.opportunity_type.value
    display_tags = [
        opportunity_type if tag == canonical.opportunity_type.value else tag
        for tag in canonical.display_tags
    ]
    return {
        "source_job_id": normalized.source_job_id,
        "title": canonical.title,
        "organization": canonical.company.name,
        "division": canonical.job_function,
        "location_text": canonical.location.raw,
        "city": canonical.location.city,
        "province": canonical.location.region_name or canonical.location.region_code,
        "country": canonical.location.country_name or canonical.location.country_code,
        "work_mode": canonical.work_mode.value,
        "salary_min": str(salary.minimum) if salary and salary.minimum is not None else None,
        "salary_max": str(salary.maximum) if salary and salary.maximum is not None else None,
        "salary_interval": salary.interval if salary else None,
        "salary_currency": salary.currency if salary else None,
        "date_posted": canonical.date_posted.isoformat() if canonical.date_posted else None,
        "application_deadline": optional_waterlooworks_text(application.get("deadline")),
        "application_url": canonical.source.direct_url,
        "application_delivery": optional_waterlooworks_text(application.get("delivery")),
        "application_documents": optional_waterlooworks_text(
            application.get("documentsRequired")
        ),
        "source_url": canonical.source.source_url,
        "description": canonical.description,
        "skill_tags": json.dumps(canonical.skill_tags, ensure_ascii=False),
        "opportunity_type": opportunity_type,
        "schedule_types": json.dumps(canonical.schedule_types, ensure_ascii=False),
        "primary_schedule_type": canonical.primary_schedule_type,
        "job_category": canonical.job_category,
        "job_subcategories": json.dumps(canonical.job_subcategories, ensure_ascii=False),
        "requirement_tags": json.dumps(canonical.requirement_tags, ensure_ascii=False),
        "display_tags": json.dumps(list(dict.fromkeys(display_tags)), ensure_ascii=False),
        "classification_version": canonical.classification_version,
        "raw_payload": payload_json,
        "first_seen_at": now,
        "last_seen_at": now,
    }


def _text(value: Any) -> str | None:
    return optional_waterlooworks_text(value)


def _encode_cursor(row_id: int) -> str:
    payload = json.dumps({"row_id": row_id}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> int:
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(value + padding))
        row_id = int(payload["row_id"])
        if row_id < 1:
            raise ValueError
        return row_id
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, binascii.Error) as error:
        raise ValueError("Invalid WaterlooWorks pagination cursor") from error


def _now() -> str:
    return datetime.now(UTC).isoformat()
