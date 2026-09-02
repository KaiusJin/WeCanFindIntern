"""Regression tests for collection retries, source circuits and result payloads."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from scripts.collection import run_collection_campaign as campaign
from wecanfindintern.db.repositories.collection_cache import LinkedInDetailCacheEntry
from wecanfindintern.ingestion.jobspy_adapter import normalize_record


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


def test_collection_window_starts_full_then_refreshes_every_ten() -> None:
    assert campaign.collection_hours_window(
        0, recent_hours=48, full_sweep_every=10
    ) is None
    assert campaign.collection_hours_window(
        1, recent_hours=48, full_sweep_every=10
    ) == 48
    assert campaign.collection_hours_window(
        9, recent_hours=48, full_sweep_every=10
    ) == 48
    assert campaign.collection_hours_window(
        10, recent_hours=48, full_sweep_every=10
    ) is None


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


def test_collection_can_defer_linkedin_descriptions(monkeypatch) -> None:
    seen = []

    def scrape(query):
        seen.append((query.linkedin_fetch_description, query.hours_old))
        return "frame", SimpleNamespace(jobs=[])

    definition = _definition("linkedin-query", "linkedin")
    definition["query"]["linkedin_fetch_description"] = True
    monkeypatch.setattr(campaign, "scrape_checked", scrape)

    asyncio.run(
        campaign.collect_all(
            [definition],
            concurrency=1,
            defer_linkedin_descriptions=True,
            hours_old_override=48,
        )
    )

    assert seen == [(False, 48)]


def test_linkedin_hydration_uses_cache_and_fetches_only_misses(monkeypatch) -> None:
    cached_job = normalize_record(
        {
            "site": "linkedin",
            "id": "li-cached",
            "job_url": "https://linkedin.test/jobs/view/cached",
            "title": "Cached job",
        }
    )
    new_job = normalize_record(
        {
            "site": "linkedin",
            "id": "li-new",
            "job_url": "https://linkedin.test/jobs/view/new",
            "title": "New job",
        }
    )
    fetched_at = datetime(2026, 9, 2, tzinfo=UTC)

    class FakeCache:
        async def linkedin_details(self, fingerprints):
            assert set(fingerprints) == {
                cached_job.source_fingerprint,
                new_job.source_fingerprint,
            }
            return {
                cached_job.source_fingerprint: LinkedInDetailCacheEntry(
                    details_fetched_at=fetched_at,
                    payload={"description": "Cached description"},
                )
            }

    fetched_ids = []

    def fetch(source_job_id):
        fetched_ids.append(source_job_id)
        return {"description": "Fresh description"}

    monkeypatch.setattr(campaign, "fetch_linkedin_details", fetch)
    hydrated, stats = asyncio.run(
        campaign.hydrate_linkedin_descriptions(
            [cached_job, new_job],
            FakeCache(),
            ttl_seconds=86_400,
            concurrency=2,
        )
    )

    assert fetched_ids == ["li-new"]
    assert [job.description for job in hydrated] == [
        "Cached description",
        "Fresh description",
    ]
    assert stats == {
        "linkedin_detail_cache_hits": 1,
        "linkedin_detail_fetched": 1,
        "linkedin_detail_failed": 0,
        "linkedin_detail_stale_fallbacks": 0,
    }


def test_linkedin_hydration_keeps_stale_detail_when_refresh_fails(monkeypatch) -> None:
    job = normalize_record(
        {
            "site": "linkedin",
            "id": "li-stale",
            "job_url": "https://linkedin.test/jobs/view/stale",
            "title": "Current title",
        }
    )
    stale_at = datetime(2025, 1, 1, tzinfo=UTC)

    class FakeCache:
        async def linkedin_details(self, _fingerprints):
            return {
                job.source_fingerprint: LinkedInDetailCacheEntry(
                    details_fetched_at=stale_at,
                    payload={"title": "Current title", "description": "Last known JD"},
                )
            }

    monkeypatch.setattr(campaign, "fetch_linkedin_details", lambda _job_id: {})
    hydrated, stats = asyncio.run(
        campaign.hydrate_linkedin_descriptions(
            [job],
            FakeCache(),
            ttl_seconds=1,
            concurrency=1,
        )
    )

    assert hydrated[0].description == "Last known JD"
    assert hydrated[0].details_fetched_at == stale_at
    assert stats["linkedin_detail_failed"] == 1
    assert stats["linkedin_detail_stale_fallbacks"] == 1
