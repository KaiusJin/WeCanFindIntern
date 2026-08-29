"""Unit tests for the WaterlooWorks module split."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from wecanfindintern.waterlooworks.collector import WaterlooWorksCollector
from wecanfindintern.waterlooworks.extractor import waterlooworks_salary
from wecanfindintern.waterlooworks.repository import (
    WaterlooWorksRepository,
    _storage_record,
)
from wecanfindintern.waterlooworks.state import (
    WaterlooWorksSnapshot,
    initial_board_states,
)


def test_initial_board_states_covers_all_boards():
    boards = initial_board_states()
    assert [board["name"] for board in boards] == [
        "full_cycle",
        "employer_student_direct",
        "graduating",
        "contract",
        "campus",
    ]


def test_snapshot_payload_round_trip():
    snapshot = WaterlooWorksSnapshot()
    payload = snapshot.payload()
    assert payload["status"] == "idle"
    assert len(payload["boards"]) == 5


def test_collector_board_state_lookup():
    snapshot = WaterlooWorksSnapshot()
    collector = WaterlooWorksCollector(
        session=None,  # type: ignore[arg-type]
        repository=None,  # type: ignore[arg-type]
        snapshot=snapshot,
    )
    state = collector._board_state("full_cycle")
    assert state["label"] == "Co-op: Full-Cycle"


def test_salary_from_rate_of_pay_per_hour_field():
    raw = {
        "id": "123456",
        "title": "Software Developer",
        "overviewFields": {"Rate Of Pay Per Hour": "18.00"},
    }
    salary = waterlooworks_salary(raw)
    assert salary is not None
    assert salary.interval == "hourly"
    assert str(salary.minimum) == "18.00"
    assert salary.currency == "CAD"


def test_salary_from_job_description():
    raw = {
        "id": "123456",
        "title": "Software Developer",
        "fullJdText": "Compensation: $25–$35 per hour, based on experience.",
    }
    salary = waterlooworks_salary(raw)
    assert salary is not None
    assert salary.interval == "hourly"
    assert str(salary.minimum) == "25"
    assert str(salary.maximum) == "35"


def test_salary_from_compensation_benefits_range():
    raw = {
        "id": "123456",
        "title": "Software Developer",
        "overviewFields": {
            "Compensation and Benefits": (
                "The salary range for this position is $70,000 - $80,000. "
                "Eligible employees also receive benefits."
            )
        },
    }
    salary = waterlooworks_salary(raw)
    assert salary is not None
    assert salary.interval == "yearly"
    assert str(salary.minimum) == "70000"
    assert str(salary.maximum) == "80000"


def test_salary_ignores_benefits_mention_and_boilerplate():
    raw = {
        "id": "123456",
        "title": "Software Developer",
        "overviewFields": {"Compensation and Benefits": "To be discussed"},
        "fullJdText": (
            "Access dental, vision, life, and disability coverage. "
            "Mental health support ($3,500 annually), virtual care, "
            "and a $750 lifestyle account."
        ),
    }
    assert waterlooworks_salary(raw) is None


def test_salary_absent_when_unknown():
    raw = {"id": "123456", "title": "Software Developer", "fullJdText": "No pay details listed."}
    assert waterlooworks_salary(raw) is None


def test_storage_record_includes_salary():
    raw = {
        "id": "123456",
        "title": "Software Developer",
        "organization": "Acme Corp",
        "location": {"city": "Waterloo", "province": "ON", "country": "Canada"},
        "sourceUrl": "https://waterlooworks.uwaterloo.ca/myAccount/co-op/full/jobs.htm",
        "jobBoard": "full_cycle",
        "overviewFields": {"Rate Of Pay Per Hour": "40.00"},
    }
    record = _storage_record(raw)
    assert record["salary_min"] == "40.00"
    assert record["salary_interval"] == "hourly"
    assert record["salary_currency"] == "CAD"


def test_existing_library_backfills_structured_salary():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "waterlooworks.sqlite3"
        connection = sqlite3.connect(db)
        connection.executescript(
            """
            CREATE TABLE waterlooworks_runs (
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
            CREATE TABLE waterlooworks_board_runs (
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
            CREATE TABLE waterlooworks_jobs (
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
            CREATE TABLE waterlooworks_job_boards (
                source_job_id TEXT NOT NULL
                    REFERENCES waterlooworks_jobs(source_job_id) ON DELETE CASCADE,
                board TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (source_job_id, board)
            );
            """
        )
        payload = json.dumps(
            {
                "id": "999999",
                "title": "Old Job",
                "overviewFields": {"Rate Of Pay Per Hour": "35.00"},
            }
        )
        connection.execute(
            """
            INSERT INTO waterlooworks_jobs(
                source_job_id, title, organization, location_text, work_mode, source_url,
                raw_payload, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "999999",
                "Old Job",
                "Old Corp",
                "Waterloo",
                "unknown",
                "https://waterlooworks.uwaterloo.ca/myAccount/co-op/full/jobs.htm",
                payload,
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )
        connection.commit()
        connection.close()

        repo = WaterlooWorksRepository(db)
        job = repo.get_job("999999")
        assert job["salary_min"] == "35.00"
        assert job["salary_interval"] == "hourly"
        assert job["salary_currency"] == "CAD"
