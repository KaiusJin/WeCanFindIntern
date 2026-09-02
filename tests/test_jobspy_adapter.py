from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pandas as pd
import pytest
from jobspy.model import JobType

from wecanfindintern.ingestion.jobspy_adapter import (
    JOBSPY_COLUMNS,
    JobSpyQuery,
    build_source_fingerprint,
    canonicalize_url,
    clean_scalar,
    dataframe_to_records,
    merge_linkedin_details,
    normalize_record,
    stabilize_jobspy_frame,
)


def sample_row() -> dict:
    row = {column: None for column in JOBSPY_COLUMNS}
    row.update(
        {
            "id": "abc123",
            "site": "indeed",
            "job_url": "https://example.com/job/abc123?utm_source=test",
            "title": "Software Engineer Intern",
            "company": "Example Co",
            "location": "Toronto, ON, Canada",
            "date_posted": date(2026, 8, 24),
            "job_type": "internship, fulltime",
            "salary_source": "direct_data",
            "interval": "hourly",
            "min_amount": 25.0,
            "max_amount": 32.0,
            "currency": "CAD",
            "is_remote": False,
            "emails": "jobs@example.com, recruiter@example.com",
            "skills": "Python, AWS",
        }
    )
    return row


def test_empty_frame_receives_stable_columns() -> None:
    stable = stabilize_jobspy_frame(pd.DataFrame())
    assert stable.empty
    assert list(stable.columns) == list(JOBSPY_COLUMNS)


def test_pandas_missing_value_becomes_none() -> None:
    assert clean_scalar(pd.NA) is None


def test_job_type_enums_become_json_safe_canonical_values() -> None:
    frame = stabilize_jobspy_frame(
        pd.DataFrame(
            [
                {
                    **sample_row(),
                    "job_type": [JobType.INTERNSHIP, JobType.FULL_TIME],
                }
            ]
        )
    )

    record = dataframe_to_records(frame)[0]

    assert record["job_type"] == ["internship", "fulltime"]
    assert normalize_record(record).employment_types == ["internship", "fulltime"]
    json.dumps(record)


def test_normalize_jobspy_record() -> None:
    frame = stabilize_jobspy_frame(pd.DataFrame([sample_row()]))
    record = dataframe_to_records(frame)[0]
    job = normalize_record(record)

    assert job.source == "indeed"
    assert job.title == "Software Engineer Intern"
    assert job.date_posted == date(2026, 8, 24)
    assert job.employment_types == ["internship", "fulltime"]
    assert job.source_skills == ["Python", "AWS"]
    assert job.salary is not None
    assert job.salary.currency == "CAD"
    assert job.salary.minimum == 25.0


def test_fingerprint_uses_source_id_when_present() -> None:
    first = build_source_fingerprint("indeed", "abc123", "https://one.example/job")
    second = build_source_fingerprint("indeed", "abc123", "https://two.example/job")
    assert first == second


def test_canonical_url_removes_tracking_parameters() -> None:
    assert (
        canonicalize_url("HTTPS://Example.COM/job/123/?utm_source=x&keep=yes#section")
        == "https://example.com/job/123?keep=yes"
    )


def test_google_requires_google_search_term() -> None:
    with pytest.raises(ValueError, match="google_search_term"):
        JobSpyQuery(sites=["google"], search_term="software engineer")


def test_indeed_rejects_incompatible_filters() -> None:
    with pytest.raises(ValueError, match="Indeed"):
        JobSpyQuery(
            sites=["indeed"],
            search_term="software engineer",
            job_type="internship",
            hours_old=24,
        )


def test_linkedin_detail_merge_preserves_current_card_metadata() -> None:
    row = sample_row()
    row.update(
        {
            "site": "linkedin",
            "id": "li-123",
            "title": "Current title",
            "description": None,
        }
    )
    job = normalize_record(row)
    fetched_at = datetime(2026, 9, 2, tzinfo=UTC)

    merged = merge_linkedin_details(
        job,
        {
            "title": "Stale cached title",
            "description": "Cached full job description",
            "job_function": "Engineering",
        },
        fetched_at=fetched_at,
    )

    assert merged.title == "Current title"
    assert merged.description == "Cached full job description"
    assert merged.job_function == "Engineering"
    assert merged.details_fetched_at == fetched_at


def test_linkedin_detail_merge_cleans_job_type_enums_for_snapshot_payload() -> None:
    row = sample_row()
    row.update({"site": "linkedin", "id": "li-123"})
    job = normalize_record(row)

    merged = merge_linkedin_details(
        job,
        {"job_type": [JobType.INTERNSHIP, JobType.FULL_TIME]},
    )

    assert merged.employment_types == ["internship", "fulltime"]
    assert merged.raw["job_type"] == ["internship", "fulltime"]
    json.dumps(merged.raw)
