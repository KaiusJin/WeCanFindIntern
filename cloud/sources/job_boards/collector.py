"""Poll every registered ATS board and return in-scope ``NormalizedJob`` records."""

from __future__ import annotations

import dataclasses
import logging
import random
import threading
import time
from collections import Counter, deque
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from cloud.sources.job_boards.boards import Board
from cloud.sources.job_boards.providers import FETCHERS, BoardResult, Scope
from wecanfindintern.domain.normalized_job import NormalizedJob

logger = logging.getLogger(__name__)

USER_AGENT = "WeCanFindIntern-job-collector/1.0 (+public job boards)"
RATE_LIMIT_STATUS = {429}
TRANSIENT_STATUS = {500, 502, 503, 504}
# Workday answers a removed career site with 422 instead of 404.
MISSING_STATUS = {404, 410, 422}
TRANSIENT_BACKOFF_SECONDS = (2, 5, 15)
# A 429 pauses only that host and retries the same request; it never fails a board.
# Workday throttles every client (residential and CSC IPs alike), so short pauses
# that relax after each success keep the queue moving; a Retry-After header from the
# host wins over the ladder.
COOLDOWN_LADDER_SECONDS = (5, 10, 20, 30)
RETRY_AFTER_MAX_SECONDS = 60
# Workday pods (wd1, wd3, wd5, ...) host many tenants and throttle per pod, so each
# pod gets a single sequential worker; the JSON board APIs tolerate a few in parallel.
WORKDAY_MIN_INTERVAL_SECONDS = 0.3


class BoardFetchError(RuntimeError):
    pass


class _DeadlineReached(Exception):
    pass


@dataclass
class AtsCollection:
    jobs: list[NormalizedJob]
    stats: Counter[str] = field(default_factory=Counter)
    failed_boards: list[str] = field(default_factory=list)
    missing_boards: list[str] = field(default_factory=list)


def host_key(board: Board) -> str:
    if board.provider == "workday":
        host = board.token.split("/", 1)[0]
        return "workday:" + host.split(".", 1)[1] if host.count(".") > 2 else "workday:" + host
    return board.provider


def _http_getter(
    session: Any,
    *,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    deadline: float | None,
    min_interval: float,
    stats: Counter[str],
    stats_lock: threading.Lock | None = None,
):
    state = {"last": 0.0, "level": 0}

    def wait(seconds: float) -> None:
        if deadline is not None and clock() + seconds > deadline:
            raise _DeadlineReached
        sleep(seconds)

    def get(url: str, params: dict[str, Any] | None = None, json: Any = None) -> Any:
        transient = 0
        last_error = ""
        while True:
            gap = state["last"] + min_interval - clock()
            if gap > 0:
                wait(gap)
            state["last"] = clock()
            try:
                if json is None:
                    response = session.get(url, params=params, timeout=30)
                else:
                    response = session.post(url, params=params, json=json, timeout=30)
                status = response.status_code
            except Exception as exc:  # connection reset, timeout, DNS
                status, last_error = None, type(exc).__name__
            if status in RATE_LIMIT_STATUS:
                if stats_lock is None:
                    stats["rate_limited"] += 1
                else:
                    with stats_lock:
                        stats["rate_limited"] += 1
                ladder = COOLDOWN_LADDER_SECONDS
                retry_after = str(response.headers.get("Retry-After", "")).strip()
                if retry_after.isdigit():
                    cooldown = min(max(int(retry_after), 1), RETRY_AFTER_MAX_SECONDS)
                else:
                    cooldown = ladder[min(state["level"], len(ladder) - 1)]
                state["level"] = min(state["level"] + 1, len(ladder) - 1)
                wait(cooldown * random.uniform(0.9, 1.2))
                continue
            if status is None or status in TRANSIENT_STATUS:
                last_error = last_error if status is None else f"HTTP {status}"
                if transient >= len(TRANSIENT_BACKOFF_SECONDS):
                    raise BoardFetchError(f"gave up after {transient + 1} attempts: {last_error}")
                wait(TRANSIENT_BACKOFF_SECONDS[transient] * random.uniform(0.8, 1.2))
                transient += 1
                continue
            if state["level"]:
                state["level"] -= 1  # every success relaxes the next cooldown one step
            if status in MISSING_STATUS:
                return None
            if status >= 400:
                raise BoardFetchError(f"HTTP {status}")
            return response.json()

    return get


def collect_ats_jobs(
    boards: Iterable[Board],
    *,
    scope: Scope | None = None,
    concurrency: int = 4,
    deadline: float | None = None,
    session_factory: Callable[[], Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    rotation_seed: int | None = None,
    progress: Callable[[str], None] | None = None,
    progress_every: float = 60.0,
) -> AtsCollection:
    """Fetch boards with one worker pool per API host.

    Each Workday pod gets one sequential worker and each JSON board API
    ``concurrency`` workers, so a throttled host only slows itself. The board
    order is rotated per run (``rotation_seed``, default: the current hour) so
    boards cut off by the deadline are reached first on a later run. A board
    that fails is reported, never fatal. ``progress`` receives a status line at
    most every ``progress_every`` seconds, so a long shard is visible in its log.
    """
    scope = dataclasses.replace(scope or Scope(), deadline=deadline, clock=clock)
    if session_factory is None:
        import requests

        def session_factory() -> Any:
            session = requests.Session()
            session.headers.update(
                {
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                }
            )
            return session

    queues: dict[str, deque[Board]] = {}
    rng = random.Random(int(time.time() // 3600) if rotation_seed is None else rotation_seed)
    for board in sorted(boards):
        queues.setdefault(host_key(board), deque()).append(board)
    for queue in queues.values():
        queue.rotate(rng.randrange(len(queue)))

    result = AtsCollection(jobs=[])
    seen: dict[str, NormalizedJob] = {}
    lock = threading.Lock()
    totals = {key: len(queue) for key, queue in queues.items()}
    done: Counter[str] = Counter()
    started = last_progress = clock()

    def report_progress() -> None:
        nonlocal last_progress
        if progress is None or clock() - last_progress < progress_every:
            return
        last_progress = clock()
        stats = result.stats
        behind = sorted(
            (key for key in totals if done[key] < totals[key]),
            key=lambda key: done[key] - totals[key],
        )
        hosts = ", ".join(f"{key.split(':')[-1]} {done[key]}/{totals[key]}" for key in behind[:4])
        progress(
            f"ATS progress {(last_progress - started) / 60:.0f} min: "
            f"{sum(done.values())}/{sum(totals.values())} boards "
            f"(ok={stats['boards_ok']}, missing={stats['boards_missing']}, "
            f"failed={stats['boards_failed']}, "
            f"skipped_deadline={stats['boards_skipped_deadline']}), "
            f"{len(seen)} jobs, {stats['rate_limited']} 429 cooldowns"
            + (f"; unfinished hosts: {hosts}" if hosts else "")
        )

    def record(board: Board, outcome: BoardResult | None, error: Exception | None) -> None:
        label = f"{board.provider}:{board.token}"
        with lock:
            done[host_key(board)] += 1
            if isinstance(error, _DeadlineReached):
                result.stats["boards_skipped_deadline"] += 1
            elif error is not None:
                result.stats["boards_failed"] += 1
                result.failed_boards.append(f"{label} ({error})")
                logger.warning("ATS board %s failed: %s", label, error)
            elif not outcome.exists:
                result.stats["boards_missing"] += 1
                result.missing_boards.append(label)
            else:
                result.stats["boards_ok"] += 1
                if outcome.truncated:
                    result.stats["boards_truncated"] += 1
                result.stats[f"{board.provider}_postings_listed"] += outcome.listed
                for job in outcome.jobs:
                    seen[job.source_fingerprint] = job
                    result.stats[f"{board.provider}_jobs_kept"] += 1
            report_progress()

    def worker(key: str, queue: deque[Board]) -> None:
        get = _http_getter(
            session_factory(),
            sleep=sleep,
            clock=clock,
            deadline=deadline,
            min_interval=WORKDAY_MIN_INTERVAL_SECONDS if key.startswith("workday:") else 0.0,
            stats=result.stats,
            stats_lock=lock,
        )
        while True:
            with lock:
                if not queue:
                    return
                board = queue.popleft()
            if deadline is not None and clock() > deadline:
                record(board, None, _DeadlineReached())
                continue
            try:
                record(board, FETCHERS[board.provider](board, get, scope), None)
            except Exception as exc:
                record(board, None, exc)

    workers = [
        (key, queue)
        for key, queue in queues.items()
        for _ in range(1 if key.startswith("workday:") else max(1, concurrency))
    ]
    with ThreadPoolExecutor(max_workers=max(1, len(workers))) as pool:
        for future in [pool.submit(worker, key, queue) for key, queue in workers]:
            future.result()
    result.jobs = sorted(seen.values(), key=lambda job: job.source_fingerprint)
    result.stats["jobs_kept"] = len(result.jobs)
    result.stats["hosts"] = len(queues)
    return result
