"""Unit tests for the WaterlooWorks module split."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from wecanfindintern.application.waterlooworks_tracker import (
    tracker_stage_for_waterlooworks_status,
)
from wecanfindintern.waterlooworks.applications import WATERLOOWORKS_APPLICATIONS_URL
from wecanfindintern.waterlooworks.browser import ChromeSession
from wecanfindintern.waterlooworks.browser_scripts import (
    WATERLOOWORKS_API_READINESS_SCRIPT,
)
from wecanfindintern.waterlooworks.collector import WaterlooWorksCollector
from wecanfindintern.waterlooworks.dates import (
    parse_waterlooworks_date,
    parse_waterlooworks_datetime,
)
from wecanfindintern.waterlooworks.extractor import (
    EXTRACT_JOBS_SCRIPT,
    _description,
    waterlooworks_salary,
)
from wecanfindintern.waterlooworks.repository import (
    WaterlooWorksRepository,
    _storage_record,
)
from wecanfindintern.waterlooworks.service import WaterlooWorksService
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


def test_status_disconnects_when_profile_process_survives_without_page():
    session = SimpleNamespace(
        load_existing_debug_port=AsyncMock(return_value=True),
        find_target=AsyncMock(return_value=None),
    )
    service = object.__new__(WaterlooWorksService)
    service.session = session
    service.snapshot = WaterlooWorksSnapshot(status="completed", browser_open=True)
    service._minimize_attempted_for_login = True
    service._lock = asyncio.Lock()

    payload = asyncio.run(service.get_status())

    assert payload["status"] == "idle"
    assert payload["browser_open"] is False
    assert payload["page_url"] is None
    assert payload["message"] == "The dedicated WaterlooWorks window is closed."
    assert service._minimize_attempted_for_login is False
    session.find_target.assert_awaited_once_with("waterlooworks.uwaterloo.ca")


def test_application_status_mapping_and_timestamp_parsing():
    assert tracker_stage_for_waterlooworks_status("Applied").value == "applied"
    assert tracker_stage_for_waterlooworks_status("Not Selected").value == "rejected"
    assert tracker_stage_for_waterlooworks_status("Selected for Interview").value == "interview"
    assert tracker_stage_for_waterlooworks_status("Employed").value == "offer"
    assert tracker_stage_for_waterlooworks_status("Unknown future status").value == "applied"
    parsed = parse_waterlooworks_datetime("May 24, 2026 4:51 PM")
    assert parsed is not None
    assert parsed.isoformat() == "2026-05-24T20:51:00+00:00"
    assert parse_waterlooworks_date("Sep 01, 2026 11:59 PM").isoformat() == "2026-09-01"


def test_chrome_session_minimizes_target_window():
    session = ChromeSession(
        profile_dir=Path("/tmp/test-waterlooworks-profile"),
        start_url="https://waterlooworks.uwaterloo.ca",
        chrome_binary=None,
    )
    session.websocket_url = "ws://127.0.0.1/devtools/browser/test"
    session.cdp_call = AsyncMock(  # type: ignore[method-assign]
        side_effect=[{"windowId": 42}, {}]
    )

    minimized = asyncio.run(session.minimize_window({"id": "page-target"}))

    assert minimized is True
    assert session.cdp_call.await_args_list[0].args[1:] == (
        "Browser.getWindowForTarget",
        {"targetId": "page-target"},
    )
    assert session.cdp_call.await_args_list[1].args[1:] == (
        "Browser.setWindowBounds",
        {"windowId": 42, "bounds": {"windowState": "minimized"}},
    )


def test_chrome_session_restores_minimized_target_before_activation():
    session = ChromeSession(
        profile_dir=Path("/tmp/test-waterlooworks-profile"),
        start_url="https://waterlooworks.uwaterloo.ca",
        chrome_binary=None,
    )
    session.websocket_url = "ws://127.0.0.1/devtools/browser/test"
    session.cdp_call = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {"windowId": 42, "bounds": {"windowState": "minimized"}},
            {},
            {},
        ]
    )

    asyncio.run(session.activate_or_create_target({"id": "page-target"}))

    assert session.cdp_call.await_args_list[1].args[1:] == (
        "Browser.setWindowBounds",
        {"windowId": 42, "bounds": {"windowState": "normal"}},
    )
    assert session.cdp_call.await_args_list[2].args[1:] == (
        "Target.activateTarget",
        {"targetId": "page-target"},
    )


def test_status_treats_minimized_profile_window_as_disconnected():
    target = {
        "id": "page-target",
        "url": "https://waterlooworks.uwaterloo.ca/myAccount/campus/jobs.htm",
    }
    session = SimpleNamespace(
        load_existing_debug_port=AsyncMock(return_value=True),
        find_target=AsyncMock(return_value=target),
        target_window_state=AsyncMock(return_value="minimized"),
    )
    service = object.__new__(WaterlooWorksService)
    service.session = session
    service.snapshot = WaterlooWorksSnapshot(status="partial", browser_open=True)
    service._minimize_attempted_for_login = True
    service._lock = asyncio.Lock()

    payload = asyncio.run(service.get_status())

    assert payload["status"] == "idle"
    assert payload["browser_open"] is False
    assert payload["page_url"] is None
    assert service._minimize_attempted_for_login is False


def test_application_navigation_reacquires_replaced_chrome_target():
    original = {
        "url": "https://waterlooworks.uwaterloo.ca/myAccount/co-op/full/jobs.htm",
        "webSocketDebuggerUrl": "ws://old",
    }
    refreshed = {
        "url": WATERLOOWORKS_APPLICATIONS_URL,
        "webSocketDebuggerUrl": "ws://new",
    }
    session = SimpleNamespace(
        navigate=AsyncMock(
            side_effect=RuntimeError("Inspected target navigated or closed")
        ),
        find_target=AsyncMock(return_value=refreshed),
    )
    service = object.__new__(WaterlooWorksService)
    service.session = session

    result = asyncio.run(service._navigate_to_applications(original))

    assert result is refreshed
    session.find_target.assert_awaited_once_with(WATERLOOWORKS_APPLICATIONS_URL)


def test_application_navigation_keeps_current_applications_target():
    target = {
        "url": WATERLOOWORKS_APPLICATIONS_URL,
        "webSocketDebuggerUrl": "ws://current",
    }
    session = SimpleNamespace(navigate=AsyncMock(), find_target=AsyncMock())
    service = object.__new__(WaterlooWorksService)
    service.session = session

    result = asyncio.run(service._navigate_to_applications(target))

    assert result is target
    session.navigate.assert_not_awaited()


def test_total_submitted_reacquires_page_after_full_navigation():
    original = {
        "url": WATERLOOWORKS_APPLICATIONS_URL,
        "webSocketDebuggerUrl": "ws://old",
    }
    refreshed = {
        "url": WATERLOOWORKS_APPLICATIONS_URL,
        "webSocketDebuggerUrl": "ws://new",
    }
    session = SimpleNamespace(
        evaluate=AsyncMock(
            side_effect=[
                RuntimeError("Inspected target navigated or closed"),
                True,
            ]
        ),
        find_target=AsyncMock(return_value=refreshed),
    )
    service = object.__new__(WaterlooWorksService)
    service.session = session

    result = asyncio.run(service._open_total_submitted(original))

    assert result is refreshed
    assert session.evaluate.await_count == 2


def test_collector_board_state_lookup():
    snapshot = WaterlooWorksSnapshot()
    collector = WaterlooWorksCollector(
        session=None,  # type: ignore[arg-type]
        repository=None,  # type: ignore[arg-type]
        snapshot=snapshot,
    )
    state = collector._board_state("full_cycle")
    assert state["label"] == "Co-op: Full-Cycle"


def test_job_extractor_uses_authenticated_api_not_responsive_results_dom():
    assert "fetch(location.pathname" in EXTRACT_JOBS_SCRIPT
    assert "dataParams" in EXTRACT_JOBS_SCRIPT
    assert "getPostingData" in EXTRACT_JOBS_SCRIPT
    assert "getPostingOverview" in EXTRACT_JOBS_SCRIPT
    assert "TextDecoder(charset)" in EXTRACT_JOBS_SCRIPT
    assert "table tbody tr" not in EXTRACT_JOBS_SCRIPT
    assert "Go to next page" not in EXTRACT_JOBS_SCRIPT
    assert ".click()" not in EXTRACT_JOBS_SCRIPT


def test_collector_keeps_all_jobs_initialization_click():
    session = SimpleNamespace(
        evaluate=AsyncMock(
            side_effect=[
                {"clicked": True},
                {"activated": True},
            ]
        )
    )
    collector = WaterlooWorksCollector(
        session=session,
        repository=None,  # type: ignore[arg-type]
        snapshot=WaterlooWorksSnapshot(),
    )

    asyncio.run(collector._click_all_jobs({}, "campus"))

    click_expression = session.evaluate.await_args_list[0].args[1]
    activation_expression = session.evaluate.await_args_list[1].args[1]
    assert ".tag-rail button" in click_expression
    assert "allJobs.click()" in click_expression
    assert 'button[aria-label="Table Mode"]' in activation_expression


def test_collector_readiness_is_api_based_and_layout_independent():
    board_url = "https://waterlooworks.uwaterloo.ca/myAccount/campus/jobs.htm"
    session = SimpleNamespace(
        evaluate=AsyncMock(
            return_value={
                "path": "/myAccount/campus/jobs.htm",
                "authenticated": True,
                "ready": True,
            }
        )
    )
    collector = WaterlooWorksCollector(
        session=session,
        repository=None,  # type: ignore[arg-type]
        snapshot=WaterlooWorksSnapshot(),
    )

    asyncio.run(collector._wait_for_board_ready({}, "campus", board_url))

    assert session.evaluate.await_args.args[1] == WATERLOOWORKS_API_READINESS_SCRIPT
    assert "table" not in WATERLOOWORKS_API_READINESS_SCRIPT.casefold()
    assert "card" not in WATERLOOWORKS_API_READINESS_SCRIPT.casefold()


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


def test_failed_waterlooworks_description_is_not_stored_as_job_text():
    assert _description("<p>There was an error loading this job posting</p>") is None


def test_storage_record_includes_salary():
    raw = {
        "id": "123456",
        "title": "Software Developer",
        "organization": "Acme Corp",
        "location": {"city": "Waterloo", "province": "ON", "country": "Canada"},
        "sourceUrl": "https://waterlooworks.uwaterloo.ca/myAccount/co-op/full/jobs.htm",
        "jobBoard": "full_cycle",
        "overviewFields": {"Rate Of Pay Per Hour": "40.00"},
        "fullJdText": "Build Python tools on Windows with Microsoft Excel.",
    }
    record = _storage_record(raw)
    assert record["salary_min"] == "40.00"
    assert record["salary_interval"] == "hourly"
    assert record["salary_currency"] == "CAD"
    assert json.loads(record["skill_tags"]) == ["python", "excel", "windows"]


def test_board_refresh_keeps_existing_waterlooworks_job_immutable(tmp_path):
    db = tmp_path / "waterlooworks.sqlite3"
    repo = WaterlooWorksRepository(db)
    run_id = repo.start_run([{"name": "full_cycle"}])
    original = {
        "id": "123456",
        "title": "Software Developer",
        "organization": "Acme Corp",
        "sourceUrl": "https://example.test/jobs/123456",
        "application": {"deadline": "Sep 01, 2026 9:00 AM"},
        "fullJdText": "Build legacy services.",
    }
    refreshed = {
        **original,
        "title": "Senior Software Developer",
        "application": {"deadline": "Sep 08, 2026 9:00 AM"},
        "fullJdText": "Build Python services with FastAPI.",
    }

    first_outcomes, _ = repo.store_board_postings(run_id, "full_cycle", [original])
    second_outcomes, _ = repo.store_board_postings(run_id, "full_cycle", [refreshed])
    repo.store_board_postings(run_id, "employer_student_direct", [refreshed])
    job = repo.get_job("123456")
    with sqlite3.connect(db) as connection:
        boards = {
            row[0]
            for row in connection.execute(
                "SELECT board FROM waterlooworks_job_boards WHERE source_job_id=?",
                ("123456",),
            )
        }

    assert job["title"] == "Software Developer"
    assert job["description"] == "Build legacy services."
    assert job["application_deadline"] == "Sep 01, 2026 9:00 AM"
    assert "python" not in job["skill_tags"]
    assert first_outcomes["posting_inserted"] == 1
    assert second_outcomes["posting_inserted"] == 0
    assert second_outcomes["posting_known"] == 1
    assert boards == {"full_cycle", "employer_student_direct"}


def test_repository_applies_search_filters_before_pagination(tmp_path):
    db = tmp_path / "waterlooworks.sqlite3"
    repo = WaterlooWorksRepository(db)
    with sqlite3.connect(db) as connection:
        rows = [
            (
                "match",
                "Backend Developer",
                "Acme Labs",
                "Toronto, Ontario, Canada",
                "Toronto",
                "Ontario",
                "Canada",
                "remote",
                "2026-08-20",
            ),
            (
                "wrong",
                "Backend Developer",
                "Other Corp",
                "Vancouver, British Columbia, Canada",
                "Vancouver",
                "British Columbia",
                "Canada",
                "onsite",
                "2026-07-01",
            ),
        ]
        connection.executemany(
            """
            INSERT INTO waterlooworks_jobs (
                source_job_id,title,organization,location_text,city,province,country,
                work_mode,date_posted,source_url,raw_payload,first_seen_at,last_seen_at
            ) VALUES (?,?,?,?,?,?,?,?,?,'https://example.com','{}','2026-01-01','2026-01-01')
            """,
            rows,
        )
        connection.execute(
            "UPDATE waterlooworks_jobs "
            "SET job_category='software_engineering', skill_tags='[\"python\"]' "
            "WHERE source_job_id='match'"
        )
        connection.executemany(
            """INSERT INTO waterlooworks_job_boards (
                source_job_id,board,first_seen_at,last_seen_at
            ) VALUES (?,?,'2026-01-01','2026-01-01')""",
            [("match", "full_cycle"), ("wrong", "graduating")],
        )

    result = repo.list_jobs(
        query="backend",
        company="Acme",
        skill="Python",
        category="software_engineering",
        city="Toronto",
        region="ON",
        country="CA",
        work_modes=["remote"],
        opportunity_types=["co_op"],
        posted_after="2026-08-01",
        limit=1,
    )
    assert result["total_count"] == 1
    assert result["has_more"] is False
    assert result["next_cursor"] is None
    assert [item["source_job_id"] for item in result["items"]] == ["match"]

    id_result = repo.list_jobs(query="match")
    assert [item["source_job_id"] for item in id_result["items"]] == ["match"]

    location_result = repo.list_jobs(location="Toronto")
    assert [item["source_job_id"] for item in location_result["items"]] == ["match"]

    board_result = repo.list_jobs(boards=["full_cycle", "graduating"])
    assert {item["source_job_id"] for item in board_result["items"]} == {"match", "wrong"}


def test_repository_uses_stable_cursor_pagination(tmp_path):
    db = tmp_path / "waterlooworks.sqlite3"
    repo = WaterlooWorksRepository(db)
    with sqlite3.connect(db) as connection:
        connection.executemany(
            """
            INSERT INTO waterlooworks_jobs (
                source_job_id,title,source_url,raw_payload,first_seen_at,last_seen_at
            ) VALUES (?,?,'https://example.com','{}','2026-01-01','2026-01-01')
            """,
            [("1", "First"), ("2", "Second"), ("3", "Third")],
        )

    first = repo.list_jobs(limit=2)
    second = repo.list_jobs(limit=2, cursor=first["next_cursor"])

    assert [item["source_job_id"] for item in first["items"]] == ["3", "2"]
    assert first["has_more"] is True
    assert first["next_cursor"]
    assert [item["source_job_id"] for item in second["items"]] == ["1"]
    assert second["has_more"] is False
    assert second["next_cursor"] is None


def test_repository_stores_submitted_application_and_missing_job(tmp_path):
    repo = WaterlooWorksRepository(tmp_path / "waterlooworks.sqlite3")
    raw = {
        "id": "471365",
        "title": "GTM Engineering",
        "organization": "Forward Inc",
        "division": "Hamming AI",
        "sourceUrl": (
            "https://waterlooworks.uwaterloo.ca/myAccount/co-op/full/applications.htm"
        ),
        "location": {"city": "Austin", "province": "Texas", "country": "United States"},
        "application": {"deadline": "May 26, 2026 9:00 AM"},
        "applicationRecord": {
            "term": "2026 - Fall",
            "appStatus": "Applied",
            "jobStatus": "Part Filled",
            "openings": "1",
            "applicationDeadline": "May 26, 2026 9:00 AM",
            "submittedAt": "May 24, 2026 4:51 PM",
            "submittedBy": "Student",
        },
        "overviewFields": {"Compensation and Benefits": "$40 - $60 CAD / hour"},
        "fullJdText": "Job Summary\nBuild GTM systems with Python and Microsoft Excel.",
    }

    stored, counts, errors = repo.store_applications([raw])

    assert errors == []
    assert counts == {"stored": 1, "new_jobs": 1, "detail_failures": 0, "failed": 0}
    assert stored[0]["application_status"] == "Applied"
    job = repo.get_job("471365")
    assert job["description"] == raw["fullJdText"]
    assert job["application_status"] == "Applied"
    assert job["skill_tags"] == ["python", "excel"]
    application_jobs = repo.list_jobs(board="applications")["items"]
    assert [item["source_job_id"] for item in application_jobs] == ["471365"]
    assert application_jobs[0]["boards"] == []


def test_application_sync_does_not_rewrite_existing_job(tmp_path):
    db = tmp_path / "waterlooworks.sqlite3"
    repo = WaterlooWorksRepository(db)
    with sqlite3.connect(db) as connection:
        connection.execute(
            """
            INSERT INTO waterlooworks_jobs (
                source_job_id,title,organization,source_url,description,skill_tags,
                raw_payload,first_seen_at,last_seen_at
            ) VALUES (
                '42','Automation Analyst','Acme','https://example.com',NULL,'[]',
                '{}','2026-01-01','2026-01-01'
            )
            """
        )
    raw = {
        "id": "42",
        "title": "Automation Analyst",
        "organization": "Acme",
        "sourceUrl": "https://example.com",
        "fullJdText": "Automate reports with Python, Microsoft Excel, and Power BI.",
        "applicationRecord": {"appStatus": "Applied"},
    }

    _, counts, errors = repo.store_applications([raw])
    job = repo.get_job("42")

    assert errors == []
    assert counts["stored"] == 1
    assert job["description"] is None
    assert job["skill_tags"] == []
    assert job["classification_version"] == 0
    assert job["application_status"] == "Applied"
    assert job["boards"] == []


def test_existing_library_adds_salary_columns_without_rewriting_jobs():
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
        assert job["salary_min"] is None
        assert job["salary_interval"] is None
        assert job["salary_currency"] is None
