"""Regression tests for collection retries, source circuits and result payloads."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from scripts.collection import run_collection_campaign as campaign


def _definition(name: str, source: str) -> dict:
    return {
        "name": name,
        "sites": [source],
        "query": {
            "search_term": "software engineer intern",
            "location": {"country_code": "CA"},
            "country_indeed": "Canada",
        },
        "page_size": 1,
        "max_results_per_source": 1,
    }


def test_permanent_provider_failure_opens_circuit(monkeypatch) -> None:
    calls = 0

    def fail_permanently(_query):
        nonlocal calls
        calls += 1
        raise RuntimeError("response status code 403")

    monkeypatch.setattr(campaign, "scrape_checked", fail_permanently)
    definitions = [_definition(f"query-{index}", "zip_recruiter") for index in range(3)]

    jobs, failures, stats = asyncio.run(
        campaign.collect_all(definitions, concurrency=1, max_retries=3)
    )

    assert jobs == []
    assert calls == 1
    assert len(failures) == 1
    assert stats == {"retried": 0, "succeeded": 0, "failed": 1, "skipped": 2}


def test_transient_provider_failure_retries_then_succeeds(monkeypatch) -> None:
    calls = 0

    def fail_then_succeed(_query):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("response status code 503")
        return "frame", SimpleNamespace(jobs=[])

    async def no_wait(_delay):
        return None

    monkeypatch.setattr(campaign, "scrape_checked", fail_then_succeed)
    monkeypatch.setattr(campaign.asyncio, "sleep", no_wait)

    jobs, failures, stats = asyncio.run(
        campaign.collect_all([_definition("query", "indeed")], concurrency=1, max_retries=2)
    )

    assert jobs == []
    assert failures == []
    assert calls == 3
    assert stats == {"retried": 2, "succeeded": 1, "failed": 0, "skipped": 0}


def test_collection_error_classification() -> None:
    assert not campaign.is_retryable_collection_error(RuntimeError("status code 400"))
    assert not campaign.is_retryable_collection_error(RuntimeError("status code 403"))
    assert not campaign.is_retryable_collection_error(RuntimeError("location not parsed"))
    assert campaign.is_retryable_collection_error(RuntimeError("status code 429"))
    assert campaign.is_retryable_collection_error(RuntimeError("status code 503"))
    assert campaign.is_retryable_collection_error(TimeoutError("read timed out"))


def test_campaign_result_payload_is_json_ready() -> None:
    result = campaign.CampaignResult(
        completed_at=datetime(2026, 9, 1, tzinfo=UTC),
        duration_seconds=12.345,
        status="partial",
        unique_jobs_collected=8,
        database_stats={"created": 6, "merged": 2, "updated": 0, "unchanged": 0},
        salary_stats={"structured": 1, "regex": 2, "deepseek": 0},
        recruiting_term_stats={
            "regex": 3,
            "deepseek": 0,
            "not_found": 4,
            "cached": 0,
            "failed": 1,
        },
        query_stats={"retried": 0, "succeeded": 2, "failed": 1, "skipped": 4},
        failures=("zip_recruiter:403",),
    )

    payload = result.payload()
    assert payload["status"] == "partial"
    assert payload["failure_count"] == 1
    assert payload["database_stats"]["created"] == 6
    assert payload["duration_seconds"] == 12.35
