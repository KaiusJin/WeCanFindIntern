"""Live-PostgreSQL integration tests for the repository layer.

Run with::

    PYTHONPATH=src python -m pytest -m db

Skipped automatically (without failing unit-test runs) when the test
database is not reachable. Point ``TEST_DATABASE_URL`` at a disposable
database — the suite applies all migrations and truncates tables between
tests, so never point it at a database with data you care about.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

import psycopg
import pytest

from wecanfindintern.config import Settings
from wecanfindintern.db.pool import Database

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://wecanfindintern:wecanfindintern@127.0.0.1:5432/wecanfindintern_test",
)
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

pytestmark = pytest.mark.db


def _database_available(url: str) -> bool:
    try:
        with psycopg.connect(url, connect_timeout=3):
            return True
    except psycopg.OperationalError:
        return False


def _apply_migrations() -> None:
    """Mirror scripts/maintenance/migrate.py for a disposable database."""

    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                checksum CHAR(64) NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );"""
        )
        applied = {
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations;"
            ).fetchall()
        }
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            connection.execute(path.read_text(encoding="utf-8"), prepare=False)
            connection.execute(
                "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s);",
                (path.name, checksum),
            )
        connection.commit()


@pytest.fixture(scope="session")
def event_loop():
    """One session-wide loop: the async pool binds to the loop that opened it."""

    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def database(event_loop):
    if not _database_available(TEST_DATABASE_URL):
        pytest.skip("TEST_DATABASE_URL is not reachable — skipping db integration tests")
    _apply_migrations()
    database = Database(Settings(database_url=TEST_DATABASE_URL))
    event_loop.run_until_complete(database.open())
    yield database
    event_loop.run_until_complete(database.close())


@pytest.fixture(scope="session")
def run(event_loop):
    """Await a coroutine on the same loop the database pool is bound to."""

    return event_loop.run_until_complete


@pytest.fixture(autouse=True)
def clean_tables(database):
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute(
            "TRUNCATE jobs, agent_sessions, agent_audit_log, interview_sessions CASCADE"
        )
        connection.commit()
    yield


def seed_job(
    *,
    title: str = "Software Engineer Intern",
    company: str = "Acme",
    region_code: str = "ON",
    region_name: str = "Ontario",
    country: str = "CA",
    description: str = "Build APIs with Python.",
) -> str:
    """Insert one minimal active job with a plain sync connection; returns public_id."""

    with psycopg.connect(TEST_DATABASE_URL) as connection:
        row = connection.execute(
            """
            INSERT INTO jobs (
                title, title_normalized, company_name, location_text, city,
                region_code, region_name, country_code, work_mode, description,
                dedupe_block_key, published_sort_at,
                first_seen_at, last_seen_at, last_verified_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, 'remote', %s,
                %s, date_trunc('day', now()), now(), now(), now()
            )
            RETURNING public_id
            """,
            (
                title,
                title.lower(),
                company,
                "Toronto, Ontario, Canada",
                "Toronto",
                region_code,
                region_name,
                country,
                description,
                os.urandom(32),
            ),
        ).fetchone()
        return str(row[0])
