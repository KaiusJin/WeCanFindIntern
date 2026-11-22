"""Regression tests for JobSpy query retries, source circuits and LinkedIn hydration."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from threading import Event, Lock
from types import SimpleNamespace

from cloud.sources.jobspy import stage
from cloud.sources.jobspy.adapter import normalize_record
from cloud.sources.jobspy.shared.exception import (
    LinkedInException,
    LinkedInRateLimitException,
)
from cloud.storage.collection_cache import LinkedInDetailCacheEntry


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

    monkeypatch.setattr(stage, "scrape_checked", fail_permanently)
    definitions = [_definition(f"query-{index}", "indeed") for index in range(3)]

    jobs, failures, stats = asyncio.run(
        stage.collect_queries(definitions, concurrency=1, max_retries=3)
    )

    assert jobs == []
    assert calls == 1
    assert len(failures) == 1
    assert {key: stats[key] for key in ("retried", "succeeded", "failed", "skipped")} == {
        "retried": 0,
        "succeeded": 0,
        "failed": 1,
        "skipped": 2,
    }


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

    monkeypatch.setattr(stage, "scrape_checked", fail_then_succeed)
    monkeypatch.setattr(stage.asyncio, "sleep", no_wait)

    jobs, failures, stats = asyncio.run(
        stage.collect_queries([_definition("query", "indeed")], concurrency=1, max_retries=2)
    )

    assert jobs == []
    assert failures == []
    assert calls == 3
    assert {key: stats[key] for key in ("retried", "succeeded", "failed", "skipped")} == {
        "retried": 2,
        "succeeded": 1,
        "failed": 0,
        "skipped": 0,
    }


def test_collection_error_classification() -> None:
    assert not stage.is_retryable_collection_error(RuntimeError("status code 400"))
    assert not stage.is_retryable_collection_error(RuntimeError("status code 403"))
    assert not stage.is_retryable_collection_error(RuntimeError("location not parsed"))
    assert stage.is_retryable_collection_error(RuntimeError("status code 429"))
    assert stage.is_retryable_collection_error(RuntimeError("status code 503"))
    assert stage.is_retryable_collection_error(TimeoutError("read timed out"))
    assert not stage.is_retryable_collection_error(
        LinkedInException("type object 'Country' has no attribute 'US_CANADA'")
    )


def test_exhausted_linkedin_rate_limit_opens_only_linkedin_circuit(monkeypatch) -> None:
    calls = []

    def scrape(query):
        source = query.sites[0]
        calls.append(source)
        if source == "linkedin":
            raise LinkedInRateLimitException("HTTP 429 circuit is open", retry_after=300)
        return "frame", SimpleNamespace(jobs=[])

    monkeypatch.setattr(stage, "scrape_checked", scrape)
    definitions = [_definition(f"linkedin-{i}", "linkedin") for i in range(3)]
    definitions.append(_definition("indeed", "indeed"))

    _, failures, stats = asyncio.run(stage.collect_queries(definitions, concurrency=4))

    assert calls.count("linkedin") == 1
    assert calls.count("indeed") == 1
    assert len(failures) == 1
    assert {key: stats[key] for key in ("retried", "succeeded", "failed", "skipped")} == {
        "retried": 0,
        "succeeded": 1,
        "failed": 1,
        "skipped": 2,
    }


def test_queued_linkedin_queries_leave_capacity_for_indeed(monkeypatch) -> None:
    indeed_finished = Event()
    counter_lock = Lock()
    active_linkedin = 0
    maximum_active = 0
    indeed_progressed = []

    def scrape(query):
        nonlocal active_linkedin, maximum_active
        if query.sites == ["indeed"]:
            indeed_finished.set()
        else:
            with counter_lock:
                active_linkedin += 1
                maximum_active = max(maximum_active, active_linkedin)
            indeed_progressed.append(indeed_finished.wait(timeout=2))
            with counter_lock:
                active_linkedin -= 1
        return "frame", SimpleNamespace(jobs=[])

    monkeypatch.setattr(stage, "scrape_checked", scrape)
    definitions = [_definition(f"linkedin-{i}", "linkedin") for i in range(3)]
    definitions.append(_definition("indeed", "indeed"))

    _, failures, stats = asyncio.run(stage.collect_queries(definitions, concurrency=2))

    assert indeed_progressed == [True, True, True]
    assert maximum_active == 1
    assert not failures
    assert stats["succeeded"] == 4


def test_detail_rate_limit_stops_fetches_and_keeps_stale_cache(monkeypatch) -> None:
    jobs = [
        normalize_record(
            {
                "site": "linkedin",
                "id": f"li-{i}",
                "title": "Intern",
                "job_url": f"https://linkedin.test/jobs/view/{i}",
            }
        )
        for i in range(3)
    ]

    class Cache:
        async def linkedin_details(self, _fingerprints):
            return {
                job.source_fingerprint: LinkedInDetailCacheEntry(
                    payload={"description": "Cached description"},
                    details_fetched_at=datetime(2020, 1, 1, tzinfo=UTC),
                )
                for job in jobs
            }

    calls = []

    def fetch(job_id):
        calls.append(job_id)
        raise LinkedInRateLimitException("HTTP 429 circuit is open")

    monkeypatch.setattr(stage, "fetch_linkedin_details", fetch)
    hydrated, stats = asyncio.run(
        stage.hydrate_linkedin_descriptions(
            jobs,
            Cache(),
            ttl_seconds=86400,
            concurrency=1,
        )
    )

    assert len(calls) == 1
    assert all(job.description == "Cached description" for job in hydrated)
    assert stats["linkedin_detail_failed"] == 3
    assert stats["linkedin_detail_stale_fallbacks"] == 3


def test_collection_can_defer_linkedin_descriptions(monkeypatch) -> None:
    seen = []

    def scrape(query):
        seen.append((query.linkedin_fetch_description, query.hours_old))
        return "frame", SimpleNamespace(jobs=[])

    definition = _definition("linkedin-query", "linkedin")
    definition["query"]["linkedin_fetch_description"] = True
    monkeypatch.setattr(stage, "scrape_checked", scrape)

    asyncio.run(
        stage.collect_queries(
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
    fetched_at = datetime.now(UTC)

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

    monkeypatch.setattr(stage, "fetch_linkedin_details", fetch)
    hydrated, stats = asyncio.run(
        stage.hydrate_linkedin_descriptions(
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

    monkeypatch.setattr(stage, "fetch_linkedin_details", lambda _job_id: {})
    hydrated, stats = asyncio.run(
        stage.hydrate_linkedin_descriptions(
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
