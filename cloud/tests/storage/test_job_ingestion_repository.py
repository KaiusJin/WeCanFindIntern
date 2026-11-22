"""Focused tests for canonical job refresh semantics."""

import asyncio
from datetime import UTC, datetime

from cloud.sources.jobspy.adapter import normalize_record
from cloud.storage.jobs import JobIngestionRepository, job_values, refresh_canonical_fields
from wecanfindintern.domain.jobs import canonical_job_from_normalized


def test_canonical_refresh_reclassifies_final_merged_evidence():
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
    current = job_values(canonical, datetime.now(UTC))
    current.update(
        {
            "description": "Build APIs",
            "employment_types": ["full_time"],
            "primary_employment_type": "full_time",
            "source_skills": ["postgresql"],
            "work_mode": "unknown",
        }
    )
    refresh_canonical_fields(current, canonical, datetime.now(UTC))

    assert current["description"] == canonical.description
    assert current["job_category"] == "software_engineering"
    assert "postgresql" in current["source_skills"]
    assert current["classification_version"] == canonical.classification_version


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
