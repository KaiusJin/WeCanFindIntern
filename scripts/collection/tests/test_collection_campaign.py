"""Regression tests for collection retries, source circuits and result payloads."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import UTC, datetime
from threading import Event, Lock

from jobspy.exception import LinkedInException, LinkedInRateLimitException

from scripts.collection import run_collection_campaign as campaign
from wecanfindintern.db.repositories.collection_cache import LinkedInDetailCacheEntry
from wecanfindintern.ingestion.jobspy_adapter import SearchPage, normalize_record


def stub_pages(monkeypatch, fetch):
    monkeypatch.setattr(
        campaign,
        "open_job_search",
        lambda query, **kwargs: nullcontext(lambda cursor: fetch(query, cursor)),
    )


def _definition(name: str, source: str) -> dict:
    return {
        "name": name,
        "sites": [source],
        "query": {
            "search_term": "software engineer intern",
            "location": {"country_code": "CA"},
            "country_indeed": "Canada",
        },
    }


def test_permanent_failure_does_not_skip_other_queries(monkeypatch) -> None:
    calls = 0

    def fail_permanently(_query, _cursor):
        nonlocal calls
        calls += 1
        raise RuntimeError("response status code 403")

    stub_pages(monkeypatch, fail_permanently)
    definitions = [_definition(f"query-{index}", "indeed") for index in range(3)]

    jobs, failures, stats = asyncio.run(
        campaign.collect_all(definitions, concurrency=1, max_retries=3)
    )

    assert jobs == []
    assert calls == 3
    assert len(failures) == 3
    assert stats == {"retried": 0, "succeeded": 0, "failed": 3, "skipped": 0}


def test_transient_provider_failure_retries_then_succeeds(monkeypatch) -> None:
    calls = 0

    def fail_then_succeed(_query, _cursor):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("response status code 503")
        return SearchPage([], None)

    async def no_wait(_delay):
        return None

    stub_pages(monkeypatch, fail_then_succeed)
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
    assert not campaign.is_retryable_collection_error(
        LinkedInException("type object 'Country' has no attribute 'US_CANADA'")
    )


def test_linkedin_recovers_after_repeated_cooldowns(monkeypatch) -> None:
    calls = []

    def scrape(query, cursor):
        source = query.sites[0]
        calls.append(source)
        if source == "linkedin" and calls.count("linkedin") <= 3:
            raise LinkedInRateLimitException("HTTP 429 circuit is open")
        return SearchPage([], None)

    waits = []

    async def sleep(delay):
        waits.append(delay)

    monkeypatch.setattr(campaign.asyncio, "sleep", sleep)
    stub_pages(monkeypatch, scrape)
    definitions = [_definition(f"linkedin-{i}", "linkedin") for i in range(3)]
    definitions.append(_definition("indeed", "indeed"))

    _, failures, stats = asyncio.run(campaign.collect_all(definitions, concurrency=4))

    assert waits == [60, 60, 60]
    assert calls.count("linkedin") == 6
    assert calls.count("indeed") == 1
    assert not failures
    assert stats == {"retried": 3, "succeeded": 4, "failed": 0, "skipped": 0}


def test_cooldown_resumes_same_page_and_all_remaining_linkedin_queries(monkeypatch):
    attempts = []
    waits = []
    job = normalize_record(
        {
            "site": "linkedin",
            "id": "li-1",
            "title": "Intern",
            "job_url": "https://example.test/1",
            "location": "Toronto, ON, Canada",
        }
    )

    def scrape(query, cursor):
        attempts.append((query.search_term, cursor))
        if attempts[-1] == ("0", None):
            return SearchPage([job], "next")
        if attempts[-1] == ("0", "next") and attempts.count(("0", "next")) == 1:
            raise LinkedInRateLimitException("429", retry_after=410)
        return SearchPage([], None)

    async def sleep(delay):
        waits.append(delay)

    stub_pages(monkeypatch, scrape)
    monkeypatch.setattr(campaign.asyncio, "sleep", sleep)
    definitions = [_definition(str(i), "linkedin") for i in range(38)]
    for definition in definitions:
        definition["query"]["search_term"] = definition["name"]
    jobs, failures, stats = asyncio.run(campaign.collect_all(definitions, concurrency=1))
    assert jobs == [job]
    assert waits == [410]
    assert [offset for term, offset in attempts if term == "0"] == [None, "next", "next"]
    assert [term for term, offset in attempts if offset is None] == [str(i) for i in range(38)]
    assert not failures
    assert stats == {"retried": 1, "succeeded": 38, "failed": 0, "skipped": 0}


def test_indeed_runs_during_linkedin_cooldown_with_one_slot(monkeypatch):
    indeed_collected = Event()
    linkedin_attempts = 0

    def scrape(query, cursor):
        nonlocal linkedin_attempts
        if query.sites == ["indeed"]:
            indeed_collected.set()
        else:
            linkedin_attempts += 1
            if linkedin_attempts == 1:
                raise LinkedInRateLimitException("429", retry_after=60)
        return SearchPage([], None)

    async def wait_for_indeed(delay):
        assert delay == 60
        assert await asyncio.to_thread(indeed_collected.wait, 2)

    stub_pages(monkeypatch, scrape)
    monkeypatch.setattr(campaign.asyncio, "sleep", wait_for_indeed)
    definitions = [_definition("first", "linkedin"), _definition("second", "indeed")]
    _, failures, stats = asyncio.run(campaign.collect_all(definitions, concurrency=1))
    assert not failures
    assert stats["succeeded"] == 2
    assert linkedin_attempts == 2


def test_long_retry_after_keeps_query_queued(monkeypatch):
    attempts = []
    waits = []

    def scrape(query, cursor):
        attempts.append(query.search_term)
        if len(attempts) == 1:
            raise LinkedInRateLimitException("429", retry_after=901)
        return SearchPage([], None)

    real_sleep = asyncio.sleep

    async def sleep(delay):
        waits.append(delay)
        await real_sleep(0)  # Other queries join the lock while the provider is cooling down.

    monkeypatch.setattr(campaign.asyncio, "sleep", sleep)
    stub_pages(monkeypatch, scrape)
    definitions = [_definition(str(i), "linkedin") for i in range(2)]
    for definition in definitions:
        definition["query"]["search_term"] = definition["name"]
    _, failures, stats = asyncio.run(campaign.collect_all(definitions))
    assert not failures
    assert waits == [901]
    assert attempts == ["0", "1", "0"]
    assert stats == {"retried": 1, "succeeded": 2, "failed": 0, "skipped": 0}


def test_queued_linkedin_queries_leave_capacity_for_indeed(monkeypatch) -> None:
    indeed_finished = Event()
    counter_lock = Lock()
    active_linkedin = 0
    maximum_active = 0
    indeed_progressed = []

    def scrape(query, cursor):
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
        return SearchPage([], None)

    stub_pages(monkeypatch, scrape)
    definitions = [_definition(f"linkedin-{i}", "linkedin") for i in range(3)]
    definitions.append(_definition("indeed", "indeed"))

    _, failures, stats = asyncio.run(campaign.collect_all(definitions, concurrency=2))

    assert indeed_progressed == [True, True, True]
    assert maximum_active == 1
    assert not failures
    assert stats["succeeded"] == 4


def test_detail_rate_limit_keeps_waiting_jobs_and_retries_interrupted_job(monkeypatch) -> None:
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
        if len(calls) == 1:
            raise LinkedInRateLimitException("HTTP 429 circuit is open")
        return {"description": "Fresh description"}

    async def sleep(delay):
        assert delay == 60

    monkeypatch.setattr(campaign.asyncio, "sleep", sleep)
    monkeypatch.setattr(campaign, "fetch_linkedin_details", fetch)
    hydrated, stats = asyncio.run(
        campaign.hydrate_linkedin_descriptions(
            jobs,
            Cache(),
            ttl_seconds=86400,
            concurrency=1,
        )
    )

    assert len(calls) == 4
    assert all(job.description == "Fresh description" for job in hydrated)
    assert stats["linkedin_detail_fetched"] == 3
    assert stats["linkedin_detail_failed"] == 0


def test_collection_window_starts_full_then_refreshes_every_ten() -> None:
    assert campaign.collection_hours_window(0, recent_hours=48, full_sweep_every=10) is None
    assert campaign.collection_hours_window(1, recent_hours=48, full_sweep_every=10) == 48
    assert campaign.collection_hours_window(9, recent_hours=48, full_sweep_every=10) == 48
    assert campaign.collection_hours_window(10, recent_hours=48, full_sweep_every=10) is None


def test_campaign_result_payload_is_json_ready() -> None:
    result = campaign.CampaignResult(
        completed_at=datetime(2026, 9, 1, tzinfo=UTC),
        duration_seconds=12.345,
        status="partial",
        unique_jobs_collected=8,
        database_stats={"created": 6, "merged": 2, "updated": 0, "unchanged": 0},
        salary_stats={"structured": 1, "regex": 2, "not_found": 0, "failed": 0},
        recruiting_term_stats={
            "regex": 3,
            "not_found": 4,
            "cached": 0,
            "failed": 1,
        },
        query_stats={"retried": 0, "succeeded": 2, "failed": 1, "skipped": 4},
        failures=("indeed:403",),
    )

    payload = result.payload()
    assert payload["status"] == "partial"
    assert payload["failure_count"] == 1
    assert payload["database_stats"]["created"] == 6
    assert payload["duration_seconds"] == 12.35


def test_collection_can_defer_linkedin_descriptions(monkeypatch) -> None:
    seen = []

    def scrape(query, cursor):
        seen.append((query.linkedin_fetch_description, query.hours_old))
        return SearchPage([], None)

    definition = _definition("linkedin-query", "linkedin")
    definition["query"]["linkedin_fetch_description"] = True
    stub_pages(monkeypatch, scrape)

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
        "linkedin_detail_removed": 0,
        "linkedin_detail_restored": 0,
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
