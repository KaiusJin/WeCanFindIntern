"""Job-board collection stage: SimplifyJobs and Stapply discovery, then poll every board."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cloud.console import log
from cloud.sources.job_boards import collect_ats_jobs, load_boards
from cloud.sources.job_boards.boards import Board, merge_boards
from cloud.sources.job_boards.providers import Scope as AtsScope
from cloud.sources.job_boards.simplify import SimplifyListing, fetch_simplify
from cloud.sources.job_boards.stapply import collect_stapply_jobs
from wecanfindintern.domain.normalized_job import NormalizedJob


def job_board_settings(defaults: dict[str, Any]) -> dict[str, Any]:
    settings = {
        "enabled": True,
        "boards_path": "job_boards.json",
        "concurrency": 8,
        "include_new_grad": False,
        "budget_seconds": 900,
        "stapply_enabled": True,
        "simplify_enabled": True,
    }
    settings.update(defaults.get("ats") or {})
    env = os.getenv("WCFI_ATS_ENABLED")
    if env is not None:
        settings["enabled"] = env.strip().lower() not in {"0", "false", "no", "off"}
    env = os.getenv("WCFI_SIMPLIFY_ENABLED")
    if env is not None:
        settings["simplify_enabled"] = env.strip().lower() not in {"0", "false", "no", "off"}
    env = os.getenv("WCFI_STAPPLY_ENABLED")
    if env is not None:
        settings["stapply_enabled"] = env.strip().lower() not in {"0", "false", "no", "off"}
    return settings


@dataclass
class BoardDiscovery:
    boards: list[Board]
    stapply_jobs: list[NormalizedJob]
    simplify_listings: list[SimplifyListing]
    stats: dict[str, int]


async def discover_boards(
    config_path: Path, settings: dict[str, Any], scope: AtsScope
) -> BoardDiscovery:
    """Committed registry + boards found in SimplifyJobs and Stapply, plus Stapply's jobs."""
    boards = load_boards(config_path.parent / settings["boards_path"])
    stapply_jobs: list[NormalizedJob] = []
    stats: dict[str, int] = {}
    simplify_listings: list[SimplifyListing] = []
    if settings["simplify_enabled"]:
        simplify_started = time.monotonic()
        try:
            simplify = await asyncio.to_thread(fetch_simplify, scope)
        except Exception as error:  # third-party list: never fail the campaign
            log(f"SimplifyJobs lists skipped: {type(error).__name__}: {error}", "WARN")
            stats["simplify_failed"] = 1
        else:
            simplify_listings = simplify.listings
            stats.update({f"simplify_{k}": int(v) for k, v in simplify.stats.items()})
            known = len(boards)
            boards = merge_boards(boards, simplify.boards)
            stats["ats_boards_from_simplify"] = len(boards) - known
            stats["simplify_seconds"] = round(time.monotonic() - simplify_started)
            log(
                f"SimplifyJobs lists loaded: {len(simplify_listings)} in-scope listings, "
                f"{len(boards) - known} new boards to poll"
            )
    if settings["stapply_enabled"]:
        # Stapply backfills every ATS/career site this collector does not poll, and
        # its Greenhouse/Lever/Ashby/Workday URLs reveal boards missing from the registry.
        stapply_started = time.monotonic()
        try:
            stapply = await asyncio.to_thread(collect_stapply_jobs, scope)
        except Exception as error:  # third-party dataset: never fail the campaign
            log(f"Stapply backfill skipped: {type(error).__name__}: {error}", "WARN")
            stats["stapply_failed"] = 1
        else:
            stapply_jobs = stapply.jobs
            stats.update({f"stapply_{k}": int(v) for k, v in stapply.stats.items()})
            known = len(boards)
            boards = merge_boards(boards, stapply.boards)
            stats["ats_boards_from_stapply"] = len(boards) - known
            stats["stapply_seconds"] = round(time.monotonic() - stapply_started)
            log(
                f"Stapply backfill complete in {time.monotonic() - stapply_started:.1f}s: "
                f"{len(stapply_jobs)} jobs, {len(boards) - known} new boards to poll"
            )
    return BoardDiscovery(boards, stapply_jobs, simplify_listings, stats)


async def poll_boards(
    boards: list[Board],
    scope: AtsScope,
    *,
    concurrency: int,
    budget_seconds: float,
    failures: list[str] | None = None,
) -> tuple[list[NormalizedJob], dict[str, int]]:
    """Poll the given boards until the budget runs out; failures are counted, not raised.

    Failed boards and the boards left unpolled at the deadline are appended to
    ``failures`` (soft failures on the ops page) when a list is given.
    """
    started = time.monotonic()
    collection = await asyncio.to_thread(
        collect_ats_jobs,
        boards,
        scope=scope,
        concurrency=concurrency,
        deadline=started + budget_seconds,
        progress=log,
    )
    stats = {f"ats_{key}": int(value) for key, value in collection.stats.items()}
    stats["ats_boards"] = len(boards)
    stats["ats_seconds"] = round(time.monotonic() - started)
    log(
        f"ATS stage complete in {time.monotonic() - started:.1f}s: "
        f"{len(collection.jobs)} jobs from {stats.get('ats_boards_ok', 0)}/{len(boards)} boards "
        f"(missing={stats.get('ats_boards_missing', 0)}, "
        f"failed={stats.get('ats_boards_failed', 0)}, "
        f"skipped_deadline={stats.get('ats_boards_skipped_deadline', 0)})"
    )
    for failure in collection.failed_boards[:50]:
        log(f"ATS board failed: {failure}", "WARN")
    skipped = stats.get("ats_boards_skipped_deadline", 0)
    if skipped:
        log(f"ATS budget of {budget_seconds:.0f}s ran out: {skipped} boards never polled", "WARN")
    if failures is not None:
        failures.extend(f"ATS board failed: {failure}" for failure in collection.failed_boards)
        if skipped:
            failures.append(
                f"ATS budget ran out: {skipped}/{len(boards)} boards never polled this run"
            )
    return collection.jobs, stats


async def collect_job_boards(
    config_path: Path, defaults: dict[str, Any]
) -> tuple[list[NormalizedJob], dict[str, int], list[SimplifyListing]]:
    """Single-process mode: discover boards, then poll all of them in one budget.

    A failed board, or an unavailable Stapply snapshot, is logged and counted but
    never fails the campaign: it is retried next run, and jobs already collected
    stay live until the normal 14-day expiry.
    """
    settings = job_board_settings(defaults)
    if not settings["enabled"]:
        log("ATS stage disabled")
        return [], {}, []
    scope = AtsScope(include_new_grad=bool(settings["include_new_grad"]))
    found = await discover_boards(config_path, settings, scope)
    jobs, stats = await poll_boards(
        found.boards,
        scope,
        concurrency=int(settings["concurrency"]),
        budget_seconds=float(settings["budget_seconds"]),
    )
    return [*jobs, *found.stapply_jobs], {**found.stats, **stats}, found.simplify_listings
