"""Focused tests for canonical job refresh semantics."""

import asyncio
from datetime import UTC, datetime

from wecanfindintern.db.repositories.jobs import JobIngestionRepository
from wecanfindintern.domain.jobs import canonical_job_from_normalized
from wecanfindintern.ingestion.jobspy_adapter import normalize_record


def test_canonical_refresh_reclassifies_final_merged_evidence_atomically():
    class FakeResult:
        def __init__(self, row=None):
            self.row = row

        async def fetchone(self):
            return self.row

    class FakeConnection:
        def __init__(self):
            self.executed = []

        async def execute(self, query, params):
            assert query.count("%s") == len(params)
            self.executed.append((query, params))
            if "SELECT title, description" in query:
                return FakeResult(
                    {
                        "title": "Python Software Engineer Intern",
                        "description": "Build APIs",
                        "employment_types": ["full_time"],
                        "primary_employment_type": "full_time",
                        "source_skills": ["postgresql"],
                        "work_mode": "unknown",
                        "description_length": 10,
                    }
                )
            return FakeResult()

    normalized = normalize_record(
        {
            "site": "indeed",
            "id": "job-1",
            "job_url": "https://example.test/jobs/1",
            "title": "Python Software Engineer Intern",
            "company": "Example",
            "location": "Toronto, ON, Canada",
            "description": "Build Python services and APIs.",
        }
    )
    canonical = canonical_job_from_normalized(
        normalized,
        scraped_at=datetime.now(UTC),
    )
    connection = FakeConnection()

    asyncio.run(
        JobIngestionRepository(None)._refresh_canonical_job(
            connection,
            1,
            canonical,
            datetime.now(UTC),
        )
    )

    update_sql, update_params = connection.executed[1]
    assert "unnest(job_subcategories ||" not in update_sql
    assert "job_category = %s" in update_sql
    assert ["postgresql"] in update_params
    assert canonical.classification_version in update_params


def test_unchanged_sources_use_one_bulk_refresh_statement():
    class FakeResult:
        async def fetchall(self):
            return [{"source_fingerprint": "ab" * 32}]

    class FakeConnection:
        def __init__(self):
            self.executed = []

        async def execute(self, query, params):
            assert query.count("%s") == len(params)
            self.executed.append((query, params))
            return FakeResult()

    normalized = normalize_record(
        {
            "site": "indeed",
            "id": "job-bulk",
            "job_url": "https://example.test/jobs/bulk",
            "title": "Software Engineer Intern",
            "company": "Example",
            "description": "Build services.",
        }
    )
    canonical = canonical_job_from_normalized(normalized).model_copy(
        update={
            "source": canonical_job_from_normalized(normalized).source.model_copy(
                update={"source_fingerprint": "ab" * 32}
            )
        }
    )
    connection = FakeConnection()

    refreshed = asyncio.run(
        JobIngestionRepository(None)._refresh_unchanged_sources(
            connection,
            jobs=[canonical],
            scraped_at=datetime.now(UTC),
        )
    )

    assert refreshed == {"ab" * 32}
    assert len(connection.executed) == 1
    assert "UPDATE job_sources" in connection.executed[0][0]
    assert "UPDATE jobs" in connection.executed[0][0]
