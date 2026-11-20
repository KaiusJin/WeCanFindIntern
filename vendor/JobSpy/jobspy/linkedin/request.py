"""Coordinate LinkedIn search and detail requests across scraper threads."""

import logging
import math
import random
import threading
import time
from datetime import timezone
from email.utils import parsedate_to_datetime

from jobspy.exception import LinkedInRateLimitException

log = logging.getLogger("JobSpy:LinkedIn")


def retry_after_seconds(value):
    """Accept both delta-seconds and HTTP-date Retry-After headers."""
    if not value:
        return 0.0
    try:
        seconds = float(value)
        return max(0.0, seconds) if math.isfinite(seconds) else 0.0
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0.0, date.timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return 0.0


class LinkedInRequestGate:
    """One process-wide AIMD limiter, backoff budget, and circuit breaker.

    Pace request starts across all workers. Allow at most two in flight
    when healthy, and only one while recovering from a rate limit.
    """

    def __init__(self):
        self._changed = threading.Condition()
        self._last_start = -math.inf
        self._not_before = 0.0
        self._in_flight = 0
        self._generation = 0
        self._state = "closed"
        self._rate = 0.2
        self._consecutive_rate_limits = 0

    @property
    def interval_seconds(self):
        return 1.0 / self._rate

    @property
    def state(self):
        return self._state

    def _admit(self):
        with self._changed:
            while True:
                now = time.monotonic()
                if self._state == "open" and now < self._not_before:
                    raise LinkedInRateLimitException(
                        "LinkedIn HTTP 429 circuit is open", retry_after=self._not_before - now
                    )
                capacity = 1 if self._consecutive_rate_limits else 2
                if self._in_flight >= capacity:
                    self._changed.wait()
                    continue
                next_start = max(self._not_before, self._last_start + self.interval_seconds)
                if next_start > now:
                    self._changed.wait(timeout=next_start - now)
                    continue
                if self._state == "open":
                    self._state = "half_open"
                self._last_start = now
                self._in_flight += 1
                return self._generation

    def _open_circuit(self):
        self._state = "open"
        now = time.monotonic()
        self._not_before = max(self._not_before, now + 300.0)
        delay = self._not_before - now
        log.warning("LinkedIn circuit opened; pausing requests for %.1fs", delay)
        raise LinkedInRateLimitException(
            f"LinkedIn HTTP 429 circuit is open; cooling down for {delay:.1f}s",
            retry_after=delay,
        )

    def _record_response(self, response, generation):
        if response.status_code == 429:
            self._generation += 1
            self._rate = max(1.0 / 60.0, self._rate * 0.5)
            self._consecutive_rate_limits += 1
            backoff = min(300.0, 60.0 * 2 ** min(self._consecutive_rate_limits - 1, 3))
            delay = max(
                backoff + random.uniform(0, 10),
                retry_after_seconds(response.headers.get("Retry-After")),
            )
            response.close()
            self._not_before = max(self._not_before, time.monotonic() + delay)
            if self._state == "half_open" or self._consecutive_rate_limits >= 3:
                self._open_circuit()
            log.warning("LinkedIn HTTP 429; waiting at least %.1fs before retry", delay)
            return

        # A success from a request already in flight when a 429 arrived
        # cannot confirm recovery or cancel the newer cooldown.
        if generation != self._generation:
            return
        if 200 <= response.status_code < 400:
            self._rate = min(1.0, self._rate + 0.02)
            self._state = "closed"
        elif self._state == "half_open":
            response.close()
            self._open_circuit()
        self._consecutive_rate_limits = 0

    def get(self, session, url, **kwargs):
        for _ in range(3):
            generation = self._admit()
            try:
                response = session.get(url, **kwargs)
            except Exception:
                with self._changed:
                    self._in_flight -= 1
                    self._changed.notify_all()
                    if self._state == "half_open":
                        self._open_circuit()
                raise
            with self._changed:
                self._in_flight -= 1
                self._changed.notify_all()
                self._record_response(response, generation)
            if response.status_code != 429:
                return response
        # Other workers may have succeeded between this request's attempts.
        # Bound retries for this URL even in that case.
        with self._changed:
            self._consecutive_rate_limits = max(1, self._consecutive_rate_limits)
            self._generation += 1
            self._open_circuit()


request_gate = LinkedInRequestGate()
