"""Exercise the vendored scraper's actual request path without network traffic."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from email.utils import format_datetime
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from requests import Response

import cloud.sources.jobspy.linkedin as linkedin
import cloud.sources.jobspy.linkedin.request as request
from cloud.sources.jobspy.adapter import JobSpyQuery, scrape_checked
from cloud.sources.jobspy.shared.exception import LinkedInRateLimitException
from cloud.sources.jobspy.shared.model import Country, Location


class Clock:
    now = 0.0
    wall_time = 1_800_000_000.0

    def __init__(self):
        self.waits = []

    def monotonic(self):
        return self.now

    def time(self):
        return self.wall_time + self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


@pytest.fixture
def gate(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(request, "time", clock)
    monkeypatch.setattr(request.random, "uniform", lambda low, high: low)
    limiter = request.LinkedInRequestGate()
    real_wait = limiter._changed.wait

    def wait(timeout=None):
        if timeout is None:
            return real_wait()
        clock.sleep(timeout)

    monkeypatch.setattr(limiter._changed, "wait", wait)
    monkeypatch.setattr(linkedin, "request_gate", limiter)
    return limiter, clock


def response(status=200, *, retry_after=None, html=""):
    result = Response()
    result.status_code = status
    result.url = "https://www.linkedin.com/jobs/view/123"
    result._content = html.encode()
    result._content_consumed = True
    if retry_after is not None:
        result.headers["Retry-After"] = retry_after
    return result


@pytest.mark.parametrize("country", list(Country))
def test_all_country_locations_can_be_displayed(country):
    rendered = Location(city="Test City", country=country).display_location()
    assert rendered.startswith("Test City")
    if country == Country.WORLDWIDE:
        assert rendered == "Test City"
    elif country == Country.CANADA:
        assert rendered == "Test City, Canada"
    elif country == Country.USA:
        assert rendered == "Test City, USA"


def test_real_linkedin_card_reaches_normalized_record(monkeypatch, gate):
    html = """
    <div class="base-search-card">
      <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/intern-123"></a>
      <span class="sr-only">Software Engineer Intern</span>
      <h4 class="base-search-card__subtitle"><a href="https://example.com">Example</a></h4>
      <div class="base-search-card__metadata">
        <span class="job-search-card__location">Toronto, ON, Canada</span>
      </div>
    </div>
    """
    monkeypatch.setattr(
        linkedin,
        "create_session",
        lambda **kwargs: SimpleNamespace(
            headers={},
            get=Mock(side_effect=[response(429), response(html=html)]),
        ),
    )

    _, result = scrape_checked(
        JobSpyQuery(
            sites=["linkedin"],
            search_term="intern",
            location="Canada",
            results_wanted=1,
        )
    )

    assert len(result.jobs) == 1
    assert result.jobs[0].location_text == "Toronto, ON, Canada"
    assert result.jobs[0].is_remote is False
    assert gate[1].waits == [60.0]


def test_search_and_detail_instances_share_aimd_rate(gate):
    limiter, clock = gate
    search, detail = linkedin.LinkedIn(), linkedin.LinkedIn()
    search.session.get = Mock(return_value=response())
    detail.session.get = Mock(return_value=response())

    search.scrape(linkedin.ScraperInput(site_type=[linkedin.Site.LINKEDIN]))
    first_interval = limiter.interval_seconds
    detail.get_job_details("123")

    assert first_interval == pytest.approx(1 / 0.22)
    assert clock.waits == pytest.approx([first_interval])
    assert limiter.interval_seconds == pytest.approx(1 / 0.24)
    assert search.session.get_adapter("https://").max_retries.total == 0
    assert detail.session.get_adapter("https://").max_retries.total == 0


def test_429_halves_rate_and_uses_exponential_backoff(gate):
    limiter, clock = gate
    session = Mock()
    session.get.side_effect = [response(429), response(429), response()]

    limiter.get(session, "https://www.linkedin.com/search")

    assert clock.waits == [60.0, 120.0]
    assert limiter.interval_seconds == pytest.approx(1 / (0.2 / 4 + 0.02))
    assert limiter.state == "closed"


def test_backoff_includes_jitter(monkeypatch, gate):
    limiter, clock = gate
    monkeypatch.setattr(request.random, "uniform", lambda low, high: high)
    session = Mock()
    session.get.side_effect = [response(429), response(429), response()]

    limiter.get(session, "https://www.linkedin.com/search")

    assert clock.waits == [70.0, 130.0]


@pytest.mark.parametrize("header_kind", ["seconds", "date"])
def test_retry_after_is_honored(gate, header_kind):
    limiter, clock = gate
    header = (
        "600"
        if header_kind == "seconds"
        else format_datetime(
            datetime.fromtimestamp(clock.time() + 600, UTC),
            usegmt=True,
        )
    )
    session = Mock()
    session.get.side_effect = [response(429, retry_after=header), response()]

    limiter.get(session, "https://www.linkedin.com/search")

    assert clock.waits == [600.0]


@pytest.mark.parametrize("header", [None, "invalid", "inf", "nan", "-1"])
def test_invalid_retry_after_falls_back_to_backoff(gate, header):
    limiter, clock = gate
    session = Mock()
    session.get.side_effect = [response(429, retry_after=header), response()]

    limiter.get(session, "https://www.linkedin.com/search")

    assert clock.waits == [60.0]


def test_circuit_blocks_all_instances_then_allows_one_probe(gate):
    limiter, clock = gate
    search, detail = linkedin.LinkedIn(), linkedin.LinkedIn()
    search.session.get = Mock(return_value=response(429, retry_after="600"))
    detail.session.get = Mock(return_value=response())

    with pytest.raises(LinkedInRateLimitException) as caught:
        search.scrape(linkedin.ScraperInput(site_type=[linkedin.Site.LINKEDIN]))
    assert search.session.get.call_count == 3
    assert limiter.state == "open"
    assert caught.value.retry_after == 600
    with pytest.raises(LinkedInRateLimitException):
        detail.get_job_details("123")
    detail.session.get.assert_not_called()

    clock.sleep(600)

    def probe(*args, **kwargs):
        assert limiter.state == "half_open"
        return response()

    detail.session.get.side_effect = probe
    detail.get_job_details("123")
    assert limiter.state == "closed"
    assert detail.session.get.call_count == 1


@pytest.mark.parametrize("probe_result", [429, 503, TimeoutError("timed out")])
def test_unsuccessful_half_open_probe_reopens_circuit(gate, probe_result):
    limiter, clock = gate
    session = Mock()
    session.get.return_value = response(429)
    with pytest.raises(LinkedInRateLimitException):
        limiter.get(session, "https://www.linkedin.com/search")
    clock.sleep(300)
    session.get.side_effect = (
        probe_result if isinstance(probe_result, Exception) else [response(probe_result)]
    )

    with pytest.raises(LinkedInRateLimitException):
        limiter.get(session, "https://www.linkedin.com/search")

    assert session.get.call_count == 4
    assert limiter.state == "open"


def test_successes_cannot_exceed_maximum_rate(gate):
    limiter, _ = gate
    session = Mock()
    session.get.return_value = response()
    for _ in range(60):
        limiter.get(session, "https://www.linkedin.com/search")
    assert limiter.interval_seconds == 1.0


def test_failed_probe_blocks_other_queued_threads(gate):
    limiter, clock = gate
    session = Mock()
    session.get.return_value = response(429)
    with pytest.raises(LinkedInRateLimitException):
        limiter.get(session, "https://www.linkedin.com/search")
    clock.sleep(300)
    session.get.reset_mock()

    def request_from_worker(_):
        with pytest.raises(LinkedInRateLimitException):
            limiter.get(session, "https://www.linkedin.com/search")

    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(request_from_worker, range(4)))

    assert session.get.call_count == 1
    assert limiter.state == "open"
    assert limiter.interval_seconds == 60.0


def test_successful_requests_from_threads_share_one_schedule(gate):
    limiter, clock = gate
    sent_at = []

    def send(*args, **kwargs):
        sent_at.append(clock.monotonic())
        return response()

    session = Mock()
    session.get.side_effect = send
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(
            workers.map(
                lambda _: limiter.get(session, "https://www.linkedin.com/search"),
                range(4),
            )
        )

    assert len(sent_at) == 4
    assert [end - start for start, end in zip(sent_at, sent_at[1:], strict=False)] == (
        pytest.approx([1 / 0.22, 1 / 0.24, 1 / 0.26])
    )


def test_slow_response_overlaps_second_request_and_cannot_undo_429(gate):
    limiter, clock = gate
    first_started = Event()
    second_throttled = Event()
    stale_completed = Event()
    starts = []

    def slow_request(*args):
        starts.append(clock.monotonic())
        first_started.set()
        assert second_throttled.wait(2)
        return response()

    def rate_limited_request(*args):
        starts.append(clock.monotonic())
        if len(starts) == 2:
            return response(429, retry_after="90")
        return response()

    def first():
        limiter.get(SimpleNamespace(get=slow_request), "search")
        stale_completed.set()

    def second():
        assert first_started.wait(2)
        limiter.get(SimpleNamespace(get=rate_limited_request), "detail")

    original_record = limiter._record_response

    def record(result, generation):
        original_record(result, generation)
        if result.status_code == 429:
            second_throttled.set()

    limiter._record_response = record
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(first), pool.submit(second)]
        for future in pending:
            future.result(timeout=5)

    assert stale_completed.is_set()
    assert starts == [0.0, 5.0, 95.0]
    assert limiter.interval_seconds == pytest.approx(1 / 0.12)
