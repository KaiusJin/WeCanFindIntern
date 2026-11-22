"""Parallel campaign: plan → discover → many collector shards → one publish step.

GitHub Actions runs each step as its own job (see ``.github/workflows/cloud-jobs.yml``):

- ``plan``      reads the database once: sweep window and LinkedIn IDs whose job
               description is still fresh in the cache; emits the LinkedIn matrix.
- ``discover``  merges the board registry with SimplifyJobs and Stapply, keeps the
               Stapply jobs, and emits the Workday matrix (one shard per pod).
- ``collect``   one shard: ``indeed``, ``linkedin`` (one search term, both countries),
               ``boards`` (Greenhouse/Lever/Ashby) or ``workday`` (one pod group).
               Shards never touch the database or any secret, and each runner has
               its own egress IP, so LinkedIn and Workday limits apply per shard.
- ``publish``   (``python -m cloud.run --from-artifacts DIR``) merges every shard,
               fills LinkedIn descriptions from the cache, runs SimplifyJobs gap
               filling, persists, publishes and writes the ops report. A missing
               shard is a failure, so the run is ``partial`` and nothing expires.

Usage: ``python -m cloud.parallel {plan,discover} OUT`` and
``python -m cloud.parallel collect KIND SHARD --plan DIR --discover DIR OUT``.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import gzip
import json
import os
import re
import sys
import time
import zlib
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cloud.config import DEFAULT_PLAN_PATH
from cloud.console import log
from cloud.planning.collection_catalog import expand_collection_catalog
from cloud.planning.sweep import collection_hours_window
from cloud.sources.job_boards.boards import Board, load_boards, save_boards
from cloud.sources.job_boards.collector import host_key
from cloud.sources.job_boards.providers import Scope
from cloud.sources.job_boards.simplify import SimplifyListing
from cloud.sources.job_boards.stage import discover_boards, job_board_settings, poll_boards
from cloud.sources.jobspy.stage import _record_score, collect_queries, hydrate_linkedin_descriptions
from wecanfindintern.domain.normalized_job import NormalizedJob

WORKDAY_SHARD_MIN_BOARDS = 80  # pods smaller than this share the "misc" shard
# A pod throttles per client IP, so a large pod is split across several runners:
# at ~3 s per board, 250 boards fit comfortably in one shard's 45-minute budget.
WORKDAY_BOARDS_PER_SHARD = 250
WORKDAY_MAX_SHARDS = 12


# --------------------------------------------------------------------------- artifacts


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def write_jobs(path: Path, jobs: list[NormalizedJob]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for job in jobs:
            handle.write(job.model_dump_json() + "\n")


def read_jobs(path: Path) -> list[NormalizedJob]:
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [NormalizedJob.model_validate_json(line) for line in handle if line.strip()]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, default=str, indent=1), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def set_output(name: str, value: Any) -> None:
    """Expose a value to later jobs (GitHub step output); printed when run locally."""
    text = json.dumps(value, separators=(",", ":"))
    target = os.getenv("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={text}\n")
    else:
        print(f"{name}={text}")


def _load_config(config_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    return expand_collection_catalog(raw), raw.get("defaults", {}) if isinstance(raw, dict) else {}


# --------------------------------------------------------------------------- plan


def linkedin_shards(definitions: list[dict[str, Any]]) -> list[dict[str, str]]:
    """One LinkedIn shard per search term (both countries run inside the shard)."""
    terms: dict[str, str] = {}
    for definition in definitions:
        if definition.get("enabled", True) and "linkedin" in definition["sites"]:
            term = definition["query"]["search_term"]
            terms.setdefault(slug(term), term)
    return [{"shard": key, "term": term} for key, term in sorted(terms.items())]


def plan(out: Path, config_path: Path = DEFAULT_PLAN_PATH) -> dict[str, Any]:
    definitions, defaults = _load_config(config_path)
    recent_hours = int(os.getenv("WCFI_COLLECTION_RECENT_HOURS", defaults.get("recent_hours", 48)))
    full_every = int(
        os.getenv("WCFI_COLLECTION_FULL_SWEEP_EVERY", defaults.get("full_sweep_every", 30))
    )
    ttl = int(defaults.get("linkedin_detail_ttl_seconds", 86_400))
    completed, fresh = 0, []
    url = os.getenv("DATABASE_URL")
    if url:
        import psycopg

        with psycopg.connect(url, autocommit=True) as db:
            db.execute("SET default_transaction_read_only = on")
            completed = db.execute(
                """
                SELECT count(*) FROM ingestion_runs
                WHERE query->>'campaign' = %s AND query ? 'sweep_number' AND status IN (1, 2)
                """,
                (config_path.name,),
            ).fetchone()[0]
            since = datetime.now(UTC) - timedelta(seconds=ttl)
            fresh = [
                row[0]
                for row in db.execute(
                    """
                    SELECT encode(source_fingerprint, 'hex') FROM job_sources
                    WHERE source = 'linkedin' AND details_fetched_at >= %s
                    """,
                    (since,),
                ).fetchall()
            ]
    hours_old = collection_hours_window(
        int(completed), recent_hours=recent_hours, full_sweep_every=full_every
    )
    data = {
        "created_at": datetime.now(UTC).isoformat(),
        "sweep_number": int(completed) + 1,
        "sweep_mode": "full" if hours_old is None else f"recent-{hours_old}h",
        "hours_old": hours_old,
        "plan_count": len(definitions),
        "linkedin": linkedin_shards(definitions),
        "fresh_linkedin": fresh,
    }
    write_json(out / "plan.json", data)
    set_output("linkedin", data["linkedin"])
    log(
        f"Plan: sweep #{data['sweep_number']} ({data['sweep_mode']}), "
        f"{len(data['linkedin'])} LinkedIn shards, {len(fresh)} fresh LinkedIn details"
    )
    return data


# --------------------------------------------------------------------------- discover


def _pod_slug(pod: str) -> str:
    host = pod.split(":", 1)[1]
    return slug(host.replace(".myworkdayjobs.com", "").replace(".myworkdaysite.com", "-site"))


def workday_shards(boards: list[Board]) -> list[dict[str, Any]]:
    """Group Workday boards by pod; split big pods, and let small pods share ``misc``."""
    pods = Counter(host_key(board) for board in boards if board.provider == "workday")
    shards: list[dict[str, Any]] = []
    rest: list[str] = []
    for pod, count in pods.most_common():
        parts = max(1, -(-count // WORKDAY_BOARDS_PER_SHARD))
        if count < WORKDAY_SHARD_MIN_BOARDS or len(shards) + parts > WORKDAY_MAX_SHARDS - 1:
            rest.append(pod)
            continue
        for index in range(parts):
            suffix = f"-{index + 1}" if parts > 1 else ""
            shards.append({"shard": _pod_slug(pod) + suffix, "pods": [pod], "part": [index, parts]})
    if rest:
        shards.append({"shard": "misc", "pods": sorted(rest), "part": [0, 1]})
    return shards


def in_part(board: Board, part: list[int]) -> bool:
    """Stable assignment of a board to one of ``part[1]`` slices of its pod."""
    index, parts = part
    digest = zlib.crc32(board.token.casefold().encode())  # stable across processes
    return digest % parts == index


def _listing_to_dict(listing: SimplifyListing) -> dict[str, Any]:
    data = dataclasses.asdict(listing)
    data["countries"] = sorted(listing.countries)
    return data


def _listing_from_dict(data: dict[str, Any]) -> SimplifyListing:
    return SimplifyListing(**{**data, "countries": set(data.get("countries") or [])})


def discover(out: Path, config_path: Path = DEFAULT_PLAN_PATH) -> dict[str, Any]:
    _, defaults = _load_config(config_path)
    settings = job_board_settings(defaults)
    scope = Scope(include_new_grad=bool(settings["include_new_grad"]))
    started = time.monotonic()
    if settings["enabled"]:
        found = asyncio.run(discover_boards(config_path, settings, scope))
        boards, stapply_jobs, listings, stats = (
            found.boards,
            found.stapply_jobs,
            found.simplify_listings,
            found.stats,
        )
    else:
        boards, stapply_jobs, listings, stats = [], [], [], {}
    out.mkdir(parents=True, exist_ok=True)
    save_boards(out / "boards.json", boards, note="Boards for this run (registry + discovery).")
    write_jobs(out / "stapply.jsonl.gz", stapply_jobs)
    write_json(out / "simplify.json", [_listing_to_dict(item) for item in listings])
    shards = workday_shards(boards)
    data = {"stats": stats, "workday": shards, "seconds": round(time.monotonic() - started)}
    write_json(out / "discover.json", data)
    set_output("workday", shards)
    log(
        f"Discover: {len(boards)} boards, {len(stapply_jobs)} Stapply jobs, "
        f"{len(listings)} SimplifyJobs listings, Workday shards {[s['shard'] for s in shards]}"
    )
    return data


# --------------------------------------------------------------------------- collect


def _definitions_for(definitions: list[dict[str, Any]], source: str, term: str | None):
    selected = []
    for definition in definitions:
        if not definition.get("enabled", True) or source not in definition["sites"]:
            continue
        if term is not None and slug(definition["query"]["search_term"]) != term:
            continue
        selected.append({**definition, "sites": [source]})
    return selected


def collect(
    kind: str,
    shard: str,
    *,
    plan_dir: Path,
    discover_dir: Path | None,
    out: Path,
    config_path: Path = DEFAULT_PLAN_PATH,
) -> dict[str, Any]:
    definitions, defaults = _load_config(config_path)
    started = time.monotonic()
    plan_data = read_json(plan_dir / "plan.json")
    jobs: list[NormalizedJob] = []
    failures: list[str] = []
    stats: dict[str, int] = {}

    if kind in {"indeed", "linkedin"}:
        selected = _definitions_for(definitions, kind, shard if kind == "linkedin" else None)
        jobs, failures, query_stats = asyncio.run(
            collect_queries(
                selected,
                concurrency=4,
                max_retries=int(defaults.get("max_attempts", 3)),
                defer_linkedin_descriptions=kind == "linkedin",
                hours_old_override=plan_data["hours_old"],
            )
        )
        stats.update(query_stats)
        if kind == "linkedin":
            jobs, detail_stats = asyncio.run(
                hydrate_linkedin_descriptions(
                    jobs,
                    None,
                    ttl_seconds=0,
                    concurrency=int(defaults.get("linkedin_detail_concurrency", 4)),
                    skip_fingerprints=frozenset(plan_data.get("fresh_linkedin") or []),
                )
            )
            stats.update(detail_stats)
    elif kind in {"boards", "workday"}:
        if discover_dir is None:
            raise ValueError("board shards need --discover")
        settings = job_board_settings(defaults)
        scope = Scope(include_new_grad=bool(settings["include_new_grad"]))
        boards = load_boards(discover_dir / "boards.json")
        if kind == "boards":
            selected_boards = [board for board in boards if board.provider != "workday"]
            budget = float(settings.get("boards_budget_seconds", 900))
        else:
            spec = next(
                item
                for item in read_json(discover_dir / "discover.json")["workday"]
                if item["shard"] == shard
            )
            pods = set(spec["pods"])
            selected_boards = [
                board
                for board in boards
                if board.provider == "workday"
                and host_key(board) in pods
                and in_part(board, spec.get("part", [0, 1]))
            ]
            budget = float(settings.get("workday_shard_budget_seconds", 2700))
        jobs, stats = asyncio.run(
            poll_boards(
                selected_boards,
                scope,
                concurrency=int(settings["concurrency"]),
                budget_seconds=budget,
            )
        )
    else:
        raise ValueError(f"unknown shard kind: {kind}")

    name = f"{kind}-{shard}" if shard else kind
    write_jobs(out / "jobs.jsonl.gz", jobs)
    result = {
        "name": name,
        "kind": kind,
        "jobs": len(jobs),
        "seconds": round(time.monotonic() - started),
        "stats": {key: int(value) for key, value in stats.items()},
        "failures": failures,
    }
    write_json(out / "result.json", result)
    log(f"Shard {name}: {len(jobs)} jobs in {result['seconds']}s, {len(failures)} failures")
    return result


# --------------------------------------------------------------------------- publish


@dataclasses.dataclass
class Collected:
    jobs: list[NormalizedJob]
    failures: list[str]
    query_stats: dict[str, int]
    listings: list[SimplifyListing]
    plan: dict[str, Any]
    shards: dict[str, dict[str, Any]]


def expected_shards(plan_data: dict[str, Any], discover_data: dict[str, Any] | None) -> list[str]:
    names = ["indeed", "boards"] if discover_data is not None else ["indeed"]
    names += [f"linkedin-{item['shard']}" for item in plan_data.get("linkedin", [])]
    if discover_data is not None:
        names += [f"workday-{item['shard']}" for item in discover_data.get("workday", [])]
    return names


def gather_artifacts(root: Path) -> Collected:
    """Merge every shard directory under ``root`` (one artifact per directory)."""
    plan_path = next(root.rglob("plan.json"), None)
    if plan_path is None:
        raise RuntimeError("plan artifact is missing; nothing to publish")
    plan_data = read_json(plan_path)
    discover_path = next(root.rglob("discover.json"), None)
    discover_data = read_json(discover_path) if discover_path else None

    best: dict[str, NormalizedJob] = {}

    def add(items: list[NormalizedJob]) -> None:
        for job in items:
            current = best.get(job.source_fingerprint)
            if current is None or _record_score(job) > _record_score(current):
                best[job.source_fingerprint] = job

    totals: Counter[str] = Counter(retried=0, succeeded=0, failed=0, skipped=0)
    failures: list[str] = []
    shards: dict[str, dict[str, Any]] = {}
    for result_path in sorted(root.rglob("result.json")):
        result = read_json(result_path)
        add(read_jobs(result_path.with_name("jobs.jsonl.gz")))
        totals.update(result.get("stats") or {})
        failures.extend(result.get("failures") or [])
        shards[result["name"]] = {
            "kind": result["kind"],
            "jobs": result["jobs"],
            "seconds": result["seconds"],
            "failures": len(result.get("failures") or []),
        }
    listings: list[SimplifyListing] = []
    if discover_data is not None:
        totals.update(discover_data.get("stats") or {})
        add(read_jobs(discover_path.with_name("stapply.jsonl.gz")))
        listings = [
            _listing_from_dict(item) for item in read_json(discover_path.with_name("simplify.json"))
        ]
        shards["discover"] = {
            "kind": "discover",
            "jobs": None,
            "seconds": discover_data.get("seconds"),
            "failures": 0,
        }
    else:
        failures.append("discover: artifact missing (SimplifyJobs/Stapply/boards skipped)")
    for name in expected_shards(plan_data, discover_data):
        if name not in shards:
            failures.append(f"{name}: shard produced no artifact (failed, cancelled or timed out)")
            shards[name] = {
                "kind": name.split("-", 1)[0],
                "jobs": None,
                "seconds": None,
                "failures": 1,
                "missing": True,
            }
    return Collected(
        jobs=sorted(best.values(), key=lambda job: job.source_fingerprint),
        failures=failures,
        query_stats=dict(totals),
        listings=listings,
        plan=plan_data,
        shards=dict(sorted(shards.items())),
    )


async def run_from_artifacts(root: Path, batch_size: int, config_path: Path = DEFAULT_PLAN_PATH):
    """Publish step: everything ``cloud.campaign.run`` does after collection."""
    from cloud.campaign import SweepInfo, finalize_sources, persist_collection
    from cloud.storage.collection_cache import CollectionCacheRepository
    from wecanfindintern.config import Settings
    from wecanfindintern.db.pool import Database

    start_time = time.monotonic()
    collected = gather_artifacts(root)
    _, defaults = _load_config(config_path)
    log(
        f"Merged {len(collected.jobs)} records from {len(collected.shards)} shards; "
        f"{len(collected.failures)} failures"
    )
    database = Database(Settings.from_env())
    await database.open()
    try:
        jobs, detail_stats = await hydrate_linkedin_descriptions(
            collected.jobs,
            CollectionCacheRepository(database.pool),
            ttl_seconds=int(defaults.get("linkedin_detail_ttl_seconds", 86_400)),
            concurrency=1,
            fetch=False,
        )
        query_stats = {
            **collected.query_stats,
            **{"linkedin_detail_cache_hits": detail_stats["linkedin_detail_cache_hits"]},
        }
        jobs, monitor = finalize_sources(
            jobs,
            collected.listings,
            query_stats,
            detail_budget_seconds=float(
                (defaults.get("ats") or {}).get("simplify_detail_budget_seconds", 600)
            ),
        )
        monitor["shards"] = collected.shards
        plan_data = collected.plan
        return await persist_collection(
            database,
            config_path=config_path,
            plan_count=plan_data.get("plan_count", 0),
            sweep=SweepInfo(
                plan_data["sweep_number"], plan_data["sweep_mode"], plan_data["hours_old"]
            ),
            normalized_jobs=jobs,
            failures=collected.failures,
            query_stats=query_stats,
            monitor=monitor,
            batch_size=batch_size,
            start_time=start_time,
        )
    finally:
        await database.close()


# --------------------------------------------------------------------------- CLI


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m cloud.parallel")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan").add_argument("out", type=Path)
    sub.add_parser("discover").add_argument("out", type=Path)
    collect_parser = sub.add_parser("collect")
    collect_parser.add_argument("kind", choices=["indeed", "linkedin", "boards", "workday"])
    collect_parser.add_argument("shard", nargs="?", default="")
    collect_parser.add_argument("--plan", type=Path, required=True)
    collect_parser.add_argument("--discover", type=Path)
    collect_parser.add_argument("out", type=Path)
    args = parser.parse_args(argv)
    if args.command == "plan":
        plan(args.out)
    elif args.command == "discover":
        discover(args.out)
    else:
        collect(args.kind, args.shard, plan_dir=args.plan, discover_dir=args.discover, out=args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
