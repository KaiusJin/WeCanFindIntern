"""Operator report for each cloud campaign: GitHub step summary + R2 ``ops/`` page.

Each run writes, next to the public feed:

- ``ops/latest.json``: the full report of the last run (success or failure);
- ``ops/history.json``: one compact row per run, newest first (capped);
- ``ops/runs/<run>-<attempt>.json``: the full report of every run (newest 500 kept);
- ``ops/index.html``: a static dashboard over these files. It is deployed by its
  own workflow (``upload-dashboard``) whenever the page changes, not per run;
- ``ops/logs/<run>-<attempt>.log``: the run's console log (gzip-encoded,
  secrets redacted), uploaded by the workflow's final step.

The report holds counts, timings, coverage, provider errors and database
aggregates, never credentials. The page is unlisted; put ``ops/*`` behind
Cloudflare Access to restrict it to operators.

CLI: ``upload-log run.log`` (collection workflow, always runs) and
``upload-dashboard`` (ops-dashboard workflow).
"""

from __future__ import annotations

import gzip
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HISTORY_LIMIT = 500
LOG_LIMIT = 200
DASHBOARD = Path(__file__).with_name("ops_dashboard.html")
SECRET_ENV = re.compile(r"(SECRET|TOKEN|PASSWORD|PASSWD|KEY|DATABASE_URL|CREDENTIAL)", re.I)
URL_CREDENTIALS = re.compile(r"(\b[a-z][a-z0-9+.-]*://)([^/\s:@]+):([^/\s@]+)@", re.I)


# --------------------------------------------------------------------------- run context


def run_context(env: dict[str, str] | None = None) -> dict[str, Any]:
    env = dict(os.environ if env is None else env)
    run_id = env.get("GITHUB_RUN_ID")
    if not run_id:
        return {"id": None, "url": None, "log_key": None, "report_key": None}
    attempt = env.get("GITHUB_RUN_ATTEMPT", "1")
    server = env.get("GITHUB_SERVER_URL", "https://github.com")
    repository = env.get("GITHUB_REPOSITORY", "")
    return {
        "id": run_id,
        "attempt": attempt,
        "event": env.get("GITHUB_EVENT_NAME"),
        "sha": (env.get("GITHUB_SHA") or "")[:12] or None,
        "url": f"{server}/{repository}/actions/runs/{run_id}" if repository else None,
        "log_key": f"ops/logs/{run_id}-{attempt}.log",
        "report_key": f"ops/runs/{run_id}-{attempt}.json",
    }


def redact(text: str, env: dict[str, str] | None = None) -> str:
    """Remove secret env values and URL credentials before a log leaves the runner."""
    env = dict(os.environ if env is None else env)
    for name, value in env.items():
        if SECRET_ENV.search(name) and value and len(value) >= 6:
            text = text.replace(value, f"[{name}]")
    return URL_CREDENTIALS.sub(r"\1[redacted]@", text)


# --------------------------------------------------------------------------- report rows


def history_row(report: dict[str, Any]) -> dict[str, Any]:
    monitor = report.get("monitor") or {}
    coverage = monitor.get("coverage") or {}
    query = report.get("query_stats") or {}
    database = report.get("database") or {}
    run = report.get("run") or {}
    return {
        "at": report.get("completed_at") or report.get("generated_at"),
        "status": report.get("status"),
        "duration_s": report.get("duration_seconds"),
        "jobs": report.get("unique_jobs_collected"),
        "created": (report.get("database_stats") or {}).get("created"),
        "db_live": database.get("jobs_live"),
        "db_new": database.get("jobs_new_this_run"),
        "db_closed": database.get("jobs_closed_this_run"),
        "db_bytes": database.get("database_bytes"),
        "feed_jobs": (report.get("feed") or {}).get("snapshot_jobs"),
        "recall": coverage.get("recall"),
        "sources": {
            name: data.get("jobs") for name, data in (monitor.get("sources") or {}).items()
        },
        "types": (database.get("breakdowns") or {}).get("opportunity_type"),
        "failed_queries": query.get("failed"),
        "ats_boards_failed": query.get("ats_boards_failed"),
        "ats_boards_skipped": query.get("ats_boards_skipped_deadline"),
        "rate_limited": query.get("ats_rate_limited"),
        "error": report.get("error"),
        "run_url": run.get("url"),
        "log_key": run.get("log_key"),
        "report_key": run.get("report_key"),
    }


def _pct(value: Any) -> str:
    return "–" if value is None else f"{value * 100:.0f}%"


def _table(title: tuple[str, ...], rows: list[tuple[Any, ...]]) -> list[str]:
    if not rows:
        return []
    return ["", "| " + " | ".join(title) + " |", "|" + "---|" * len(title)] + [
        "| " + " | ".join("–" if cell is None else str(cell) for cell in row) + " |" for row in rows
    ]


def render_markdown(report: dict[str, Any]) -> str:
    monitor = report.get("monitor") or {}
    coverage = monitor.get("coverage") or {}
    query = report.get("query_stats") or {}
    database = report.get("database") or {}
    lines = [
        f"## Job collection: {report.get('status', 'unknown')}",
        "",
        f"- Duration: {report.get('duration_seconds', '–')} s",
        f"- Unique source records: {report.get('unique_jobs_collected', '–')}",
    ]
    if database:
        lines.append(
            f"- Database: {database.get('jobs_live')} live jobs "
            f"({database.get('jobs_new_this_run')} new, "
            f"{database.get('jobs_updated_this_run')} updated, "
            f"{database.get('jobs_closed_this_run')} closed this run); "
            f"size {round((database.get('database_bytes') or 0) / 2**20)} MiB"
        )
    if report.get("error"):
        lines.append(f"- Error: `{report['error']}`")
    if coverage:
        lines.append(
            f"- SimplifyJobs coverage (before gap fill): {coverage.get('covered')}/"
            f"{coverage.get('reference')} ({_pct(coverage.get('recall'))})"
        )
    sources = monitor.get("sources") or {}
    lines += _table(
        ("Collector", "Jobs", "After gap fill", "Simplify recall"),
        [
            (n, d.get("jobs"), d.get("final_jobs"), _pct(d.get("simplify_recall")))
            for n, d in sources.items()
        ],
    )
    lines += _table(
        ("DB source", "Live jobs", "Seen this run", "New this run"),
        [
            (n, d["live_jobs"], d["seen_this_run"], d["new_this_run"])
            for n, d in (database.get("sources") or {}).items()
        ],
    )
    types = (database.get("breakdowns") or {}).get("opportunity_type") or {}
    lines += _table(("Opportunity type", "Live jobs"), list(types.items()))
    lines += _table(
        ("Platform (Simplify)", "Reference", "Covered", "Recall"),
        [
            (n, d["reference"], d["covered"], _pct(d["recall"]))
            for n, d in (coverage.get("by_platform") or {}).items()
        ],
    )
    health = [
        (
            "Indeed queries ok / failed",
            f"{query.get('indeed_succeeded', 0)} / {query.get('indeed_failed', 0)}",
        ),
        (
            "LinkedIn queries ok / failed",
            f"{query.get('linkedin_succeeded', 0)} / {query.get('linkedin_failed', 0)}",
        ),
        (
            "ATS boards ok / failed",
            f"{query.get('ats_boards_ok', 0)} / {query.get('ats_boards_failed', 0)}",
        ),
        (
            "ATS boards missing / skipped at deadline",
            f"{query.get('ats_boards_missing', 0)} / {query.get('ats_boards_skipped_deadline', 0)}",
        ),
        ("ATS 429 cooldowns", query.get("ats_rate_limited", 0)),
        ("Stapply jobs", query.get("stapply_jobs_kept")),
        ("Simplify gap-fill jobs", query.get("simplify_gap_fill_jobs")),
    ]
    lines += _table(("Health", "Value"), health)
    run = report.get("run") or {}
    if run.get("log_key"):
        lines += ["", f"Run log: uploaded to `{run['log_key']}` (see the ops page)."]
    failures = report.get("failures") or []
    if failures:
        lines += ["", f"<details><summary>{len(failures)} failures</summary>", ""]
        lines += [f"- {item}" for item in failures[:50]]
        lines += ["", "</details>"]
    misses = coverage.get("missed_sample") or []
    if misses:
        lines += ["", "<details><summary>Missed SimplifyJobs sample</summary>", ""]
        lines += [f"- {m['company']}: {m['title']} ({m['platform']})" for m in misses]
        lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- outputs


def write_step_summary(report: dict[str, Any]) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(render_markdown(report))


def publish_ops(store: Any, report: dict[str, Any]) -> None:
    """Write this run's report (per-run file, latest, history). Never raises.

    The dashboard page itself is deployed separately (``upload-dashboard``), so
    it is reachable before the first run and does not change with each run.
    """
    try:
        now = datetime.now(UTC)
        run = {**run_context(), **(report.get("run") or {})}
        if not run.get("report_key"):  # local or manual run outside GitHub Actions
            run["report_key"] = f"ops/runs/local-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
        report = {"generated_at": now.isoformat(), **report, "run": run}
        body = json.dumps(report, default=str).encode()
        raw = store.read("ops/history.json")
        history = json.loads(raw) if raw else []
        history = [history_row(report), *history][:HISTORY_LIMIT]
        store.put_ops(report["run"]["report_key"], body, "application/json")
        store.put_ops("ops/latest.json", body, "application/json")
        store.put_ops(
            "ops/history.json", json.dumps(history, default=str).encode(), "application/json"
        )
        store.keep_newest("ops/runs/", HISTORY_LIMIT)
    except Exception as error:  # pragma: no cover - logged, never fatal
        print(f"ops report upload failed: {type(error).__name__}: {error}", flush=True)


def upload_dashboard(store: Any) -> str:
    store.put_ops("ops/index.html", DASHBOARD.read_bytes(), "text/html; charset=utf-8")
    return "ops/index.html"


def upload_log(store: Any, path: Path, *, env: dict[str, str] | None = None) -> str | None:
    """Upload the redacted run log (gzip-encoded text) and keep the newest LOG_LIMIT logs."""
    key = run_context(env).get("log_key")
    if not key or not path.exists():
        return None
    text = redact(path.read_text(encoding="utf-8", errors="replace"), env)
    store.put_ops(
        key,
        gzip.compress(text.encode("utf-8"), compresslevel=6),
        "text/plain; charset=utf-8",
        content_encoding="gzip",
    )
    store.keep_newest("ops/logs/", LOG_LIMIT)
    return key


USAGE = (
    "usage: python -m scripts.cloud.ops_report upload-log <path>\n"
    "       python -m scripts.cloud.ops_report upload-dashboard"
)


def main(argv: list[str]) -> int:
    from scripts.cloud.publish import r2_from_env

    if argv[:1] == ["upload-dashboard"] and len(argv) == 1:
        # Deployment step: a failure here should fail the workflow.
        print(f"dashboard uploaded: {upload_dashboard(r2_from_env())}", flush=True)
        return 0
    if argv[:1] != ["upload-log"] or len(argv) != 2:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        key = upload_log(r2_from_env(), Path(argv[1]))
    except Exception as error:  # never fail the workflow because of the log copy
        print(f"run log upload failed: {type(error).__name__}: {error}", flush=True)
        return 0
    print(f"run log uploaded: {key}" if key else "no run log uploaded", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
