"""Operator report for each cloud campaign: GitHub step summary + R2 ``ops/`` page.

Each run writes, next to the public feed:

- ``ops/latest.json``: the full report of the last run (success or failure);
- ``ops/history.json``: one compact row per run, newest first (capped);
- ``ops/runs/<run>-<attempt>.json``: the full report of every run (newest 500 kept);
- ``ops/index.html``: a static dashboard over these files. It is deployed by its
  own workflow (``upload-dashboard``) whenever the page changes, not per run;
- ``ops/logs/<run>-<attempt>.log``: the publish job's console log (gzip-encoded,
  secrets redacted), uploaded by the workflow's final step;
- ``ops/logs/<run>-<attempt>/<job>.log`` and ``.../index.json``: the log of every
  other job of the run (plan, discover, each collector shard, merge, gap fill),
  fetched from the GitHub API by the same step, with per-log counts of warnings,
  errors and 429s and the last notable lines.

Each report also carries ``checks``: derived health findings (budget cut-offs,
missing JDs, low recall, ...) so a green run that silently skipped work still
shows up as a warning on the page and in the step summary.

The report holds counts, timings, coverage, provider errors and database
aggregates, never credentials. The page is unlisted; put ``ops/*`` behind
Cloudflare Access to restrict it to operators.

CLI: ``upload-log run.log`` (collection workflow, always runs; needs
``GITHUB_TOKEN`` with ``actions: read`` for the other jobs' logs) and
``upload-dashboard`` (ops-dashboard workflow).
"""

from __future__ import annotations

import gzip
import json
import os
import re
import sys
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HISTORY_LIMIT = 500
# Log objects kept under ops/logs/ (each run writes one per GitHub job, ~60).
LOG_LIMIT = 6000
LOG_MAX_BYTES = 16 * 2**20
DB_WARN_BYTES = 400 * 2**20  # Supabase free tier: 500 MB per project
DASHBOARD = Path(__file__).with_name("dashboard.html")
SECRET_ENV = re.compile(r"(SECRET|TOKEN|PASSWORD|PASSWD|KEY|DATABASE_URL|CREDENTIAL)", re.I)
URL_CREDENTIALS = re.compile(r"(\b[a-z][a-z0-9+.-]*://)([^/\s:@]+):([^/\s@]+)@", re.I)


# --------------------------------------------------------------------------- run context


def run_context(env: dict[str, str] | None = None) -> dict[str, Any]:
    env = dict(os.environ if env is None else env)
    run_id = env.get("GITHUB_RUN_ID")
    if not run_id:
        return {"id": None, "url": None, "log_key": None, "logs_key": None, "report_key": None}
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
        "logs_key": f"ops/logs/{run_id}-{attempt}/index.json",
        "report_key": f"ops/runs/{run_id}-{attempt}.json",
    }


def redact(text: str, env: dict[str, str] | None = None) -> str:
    """Remove secret env values and URL credentials before a log leaves the runner."""
    env = dict(os.environ if env is None else env)
    for name, value in env.items():
        if SECRET_ENV.search(name) and value and len(value) >= 6:
            text = text.replace(value, f"[{name}]")
    return URL_CREDENTIALS.sub(r"\1[redacted]@", text)


# --------------------------------------------------------------------------- health checks


def _share(part: Any, whole: Any) -> float | None:
    return part / whole if part is not None and whole else None


def _n(value: Any) -> str:
    return f"{int(value):,}" if value is not None else "–"


def assess(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Health findings for one report, worst first: ``{level, area, message, detail}``.

    ``error`` means data is missing or wrong for this run; ``warn`` means the run
    did less than it should (a budget ran out, JDs missing); ``info`` is context.
    A run can be green in GitHub Actions and still carry warnings here.
    """
    checks: list[dict[str, Any]] = []

    def add(level: str, area: str, message: str, detail: list[str] | None = None) -> None:
        checks.append({"level": level, "area": area, "message": message, "detail": detail or []})

    q = report.get("query_stats") or {}
    monitor = report.get("monitor") or {}
    shards = monitor.get("shards") or {}
    coverage = monitor.get("coverage") or {}
    database = report.get("database") or {}

    if report.get("error"):
        add("error", "run", f"Run failed: {report['error']}")
    if report.get("database_error"):
        add("warn", "database", f"Database statistics unavailable: {report['database_error']}")
    hard = list(report.get("hard_failures") or [])
    if hard:
        add("error", "shards", f"{len(hard)} collector steps produced nothing", hard)
    soft = [f for f in report.get("failures") or [] if f not in hard]
    if soft:
        add("warn", "collect", f"{len(soft)} soft failures (queries, boards, retry steps)", soft)

    boards, skipped = q.get("ats_boards") or 0, q.get("ats_boards_skipped_deadline") or 0
    if skipped:
        by_shard = sorted(
            (
                (name, (s.get("stats") or {}).get("ats_boards_skipped_deadline", 0), s)
                for name, s in shards.items()
            ),
            key=lambda item: -item[1],
        )
        detail = [
            f"{name}: {_n(n)}/{_n((s.get('stats') or {}).get('ats_boards'))} boards skipped "
            f"after {_n(s.get('seconds'))} s"
            for name, n, s in by_shard
            if n
        ]
        share = skipped / boards if boards else 1.0
        add(
            "error" if share >= 0.1 else "warn",
            "ats",
            f"{_n(skipped)} of {_n(boards)} ATS boards ({share:.0%}) were never polled: "
            "the shard's time budget ran out",
            detail,
        )
    at_budget = [
        f"{name}: {_n(s.get('seconds'))} s of {_n(s.get('budget_seconds'))} s"
        for name, s in shards.items()
        if s.get("budget_seconds") and (s.get("seconds") or 0) >= 0.97 * s["budget_seconds"]
    ]
    if at_budget:
        add("warn", "budget", f"{len(at_budget)} shards stopped at their time budget", at_budget)
    failed, missing = q.get("ats_boards_failed") or 0, q.get("ats_boards_missing") or 0
    if failed:
        add("warn", "ats", f"{_n(failed)} ATS boards failed (see the shard logs)")
    if missing:
        add("info", "ats", f"{_n(missing)} ATS boards no longer exist (404/410/422)")
    if q.get("ats_rate_limited"):
        add("info", "ats", f"{_n(q['ats_rate_limited'])} ATS 429 cooldowns")

    for name in ("stapply", "simplify"):
        if q.get(f"{name}_failed"):
            add("error", "discover", f"{name.title()} snapshot unavailable this run")

    requested = q.get("simplify_detail_requested") or 0
    found = q.get("simplify_detail_found") or 0
    if requested and found / requested < 0.8:
        missing_by = sorted(
            (
                (k.removeprefix("simplify_detail_missing_"), v)
                for k, v in q.items()
                if k.startswith("simplify_detail_missing_") and v
            ),
            key=lambda item: -item[1],
        )
        add(
            "warn",
            "jd",
            f"Only {_n(found)} of {_n(requested)} gap-fill jobs ({found / requested:.0%}) "
            "got a description",
            [f"{platform}: {_n(n)} without JD" for platform, n in missing_by],
        )

    li_missing = sum(
        q.get(k) or 0 for k in ("linkedin_retry_detail_failed", "linkedin_retry_detail_unfinished")
    )
    if li_missing:
        add("warn", "linkedin", f"{_n(li_missing)} LinkedIn jobs still have no JD after the retry")
    for site in ("indeed", "linkedin"):
        if q.get(f"{site}_failed"):
            add("warn", site, f"{_n(q[f'{site}_failed'])} {site.title()} queries failed")
    if q.get("linkedin_skipped"):
        add("warn", "linkedin", f"{_n(q['linkedin_skipped'])} LinkedIn queries skipped at deadline")

    recall = coverage.get("recall")
    if recall is not None and recall < 0.6:
        add("warn", "coverage", f"SimplifyJobs recall before gap fill is {recall:.0%}")
    weak = [
        f"{name}: {p['covered']}/{p['reference']} ({p['recall']:.0%})"
        for name, p in (coverage.get("by_platform") or {}).items()
        if p.get("reference", 0) >= 50 and p.get("recall", 1) < 0.5
    ]
    if weak:
        add("warn", "coverage", f"Low recall on {len(weak)} platforms", weak)

    if (database.get("database_bytes") or 0) >= DB_WARN_BYTES:
        add("warn", "database", f"Database is {database['database_bytes'] / 2**20:.0f} MiB")
    live = database.get("jobs_live") or 0
    quality = database.get("quality") or {}
    no_jd = _share(quality.get("no_description"), live)
    if no_jd is not None and no_jd >= 0.15:
        add("warn", "quality", f"{no_jd:.0%} of live jobs have no usable description")
    if report.get("status") in {"success", "partial"} and not report.get("feed"):
        add("error", "feed", "No feed was published for this run")

    order = {"error": 0, "warn": 1, "info": 2}
    return sorted(checks, key=lambda c: order[c["level"]])


# --------------------------------------------------------------------------- report rows


def wall_seconds(report: dict[str, Any]) -> float | None:
    """End-to-end run time: plan job start → publish finish (parallel runs only)."""
    started = (report.get("monitor") or {}).get("started_at")
    finished = report.get("completed_at") or report.get("generated_at")
    if not started or not finished:
        return None
    try:
        delta = datetime.fromisoformat(str(finished)) - datetime.fromisoformat(str(started))
    except ValueError:
        return None
    return round(delta.total_seconds())


def history_row(report: dict[str, Any]) -> dict[str, Any]:
    monitor = report.get("monitor") or {}
    coverage = monitor.get("coverage") or {}
    query = report.get("query_stats") or {}
    database = report.get("database") or {}
    run = report.get("run") or {}
    return {
        "at": report.get("completed_at") or report.get("generated_at"),
        "status": report.get("status"),
        "duration_s": wall_seconds(report) or report.get("duration_seconds"),
        "publish_s": report.get("duration_seconds"),
        "jobs": report.get("unique_jobs_collected"),
        "created": (report.get("database_stats") or {}).get("created"),
        "db_live": database.get("jobs_live"),
        "db_total": database.get("jobs_total"),
        "db_new": database.get("jobs_new_this_run"),
        "db_closed": database.get("jobs_closed_this_run"),
        "db_archived": database.get("jobs_archived_this_run"),
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
        "ats_boards": query.get("ats_boards"),
        "checks_error": sum(1 for c in report.get("checks") or [] if c.get("level") == "error"),
        "checks_warn": sum(1 for c in report.get("checks") or [] if c.get("level") == "warn"),
        "error": report.get("error"),
        "run_url": run.get("url"),
        "log_key": run.get("log_key"),
        "report_key": run.get("report_key"),
        "logs_key": run.get("logs_key"),
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
        f"- Duration: {wall_seconds(report) or report.get('duration_seconds', '–')} s end to end "
        f"({report.get('duration_seconds', '–')} s publish)",
        f"- Unique source records: {report.get('unique_jobs_collected', '–')}",
    ]
    if database:
        lines.append(
            f"- Database: {database.get('jobs_live')} live jobs "
            f"({database.get('jobs_new_this_run')} new, "
            f"{database.get('jobs_updated_this_run')} updated, "
            f"{database.get('jobs_archived_this_run')} archived this run, "
            f"{database.get('jobs_archived_total')} in archive); "
            f"size {round((database.get('database_bytes') or 0) / 2**20)} MiB"
        )
    if report.get("error"):
        lines.append(f"- Error: `{report['error']}`")
    checks = report.get("checks")
    if checks is None:
        checks = assess(report)
    icons = {"error": "🔴", "warn": "🟠", "info": "ℹ️"}
    if checks:
        lines += ["", "### Checks", ""]
        lines += [f"- {icons[c['level']]} {c['message']}" for c in checks]
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
        report["checks"] = assess(report)
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


# --------------------------------------------------------------------------- job logs

ERROR_LINE = re.compile(
    r"\[(ERROR|CRITICAL)\]|##\[error\]|Traceback \(most recent call last\)|\b\w+(Error|Exception): "
)
WARN_LINE = re.compile(r"\[WARN(ING)?\]|##\[warning\]|::warning")
RATE_LINE = re.compile(r"\b429\b|rate.?limit", re.I)
# GitHub runner chatter that is not about this project (e.g. action deprecations).
NOISE_LINE = re.compile(r"Node\.js \d+ (is|actions are) deprecated|is deprecated and will be", re.I)
GITHUB_TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ")


def summarize_log(text: str, *, highlights: int = 15) -> dict[str, Any]:
    """Line counts by kind plus the last notable lines (errors first, then warnings)."""
    lines = text.splitlines()
    errors: list[str] = []
    warnings: list[str] = []
    rate = 0
    for line in lines:
        if NOISE_LINE.search(line):
            continue
        if RATE_LINE.search(line):
            rate += 1
        if ERROR_LINE.search(line):
            errors.append(line)
        elif WARN_LINE.search(line):
            warnings.append(line)
    picked = (errors[-highlights:] + warnings[-highlights:])[:highlights]
    return {
        "lines": len(lines),
        "errors": len(errors),
        "warnings": len(warnings),
        "rate_limits": rate,
        "highlights": [GITHUB_TIMESTAMP.sub("", line)[:400] for line in picked],
    }


def _clip(text: str) -> str:
    data = text.encode("utf-8")
    if len(data) <= LOG_MAX_BYTES:
        return text
    head, tail = data[: 2 * 2**20], data[-(LOG_MAX_BYTES - 2 * 2**20) :]
    return (
        head.decode("utf-8", "replace")
        + f"\n... [{len(data) - len(head) - len(tail):,} bytes cut] ...\n"
        + tail.decode("utf-8", "replace")
    )


class _DropAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """GitHub redirects log downloads to signed storage URLs: never forward the token."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.remove_header("Authorization")
        return new


def github_fetch(url: str, token: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.build_opener(_DropAuthOnRedirect).open(request, timeout=60) as response:
        return response.read()


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "job"


def _seconds(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    parse = lambda v: datetime.fromisoformat(v.replace("Z", "+00:00"))  # noqa: E731
    return round((parse(end) - parse(start)).total_seconds())


def upload_job_logs(
    store: Any,
    publish_log: Path | None,
    *,
    env: dict[str, str] | None = None,
    fetch: Callable[[str, str], bytes] = github_fetch,
) -> str | None:
    """Upload every finished job's log of this run attempt and an ``index.json``.

    The publish job is still running, so its entry is ``publish_log`` (run.log).
    Never raises for a single job: a log that cannot be fetched is listed with
    its error instead.
    """
    env = dict(os.environ if env is None else env)
    context = run_context(env)
    if not context.get("logs_key"):
        return None
    prefix = context["logs_key"].rsplit("/", 1)[0]
    entries: list[dict[str, Any]] = []
    token, repository = env.get("GITHUB_TOKEN"), env.get("GITHUB_REPOSITORY")
    api = env.get("GITHUB_API_URL", "https://api.github.com")
    jobs: list[dict[str, Any]] = []
    listing_error = None
    if token and repository:
        try:
            page = 1
            while True:
                data = json.loads(
                    fetch(
                        f"{api}/repos/{repository}/actions/runs/{context['id']}/attempts/"
                        f"{context['attempt']}/jobs?per_page=100&page={page}",
                        token,
                    )
                )
                jobs += data.get("jobs") or []
                if len(jobs) >= data.get("total_count", 0) or not data.get("jobs"):
                    break
                page += 1
        except Exception as error:  # the publish log below is still uploaded
            listing_error = f"{type(error).__name__}: {error}"[:300]
    else:
        listing_error = "GITHUB_TOKEN or GITHUB_REPOSITORY not set"

    def one(job: dict[str, Any]) -> dict[str, Any]:
        entry = {
            "name": job.get("name"),
            "conclusion": job.get("conclusion"),
            "started_at": job.get("started_at"),
            "completed_at": job.get("completed_at"),
            "seconds": _seconds(job.get("started_at"), job.get("completed_at")),
            "url": job.get("html_url"),
            "failed_steps": [
                step.get("name")
                for step in job.get("steps") or []
                if step.get("conclusion") in {"failure", "cancelled", "timed_out"}
            ],
        }
        try:
            raw = fetch(f"{api}/repos/{repository}/actions/jobs/{job['id']}/logs", token or "")
            text = _clip(redact(raw.decode("utf-8", "replace"), env))
            key = f"{prefix}/{_slug(str(job.get('name')))}-{job['id']}.log"
            store.put_ops(
                key,
                gzip.compress(text.encode("utf-8"), compresslevel=6),
                "text/plain; charset=utf-8",
                content_encoding="gzip",
            )
            entry.update(key=key, **summarize_log(text))
        except Exception as error:
            entry["log_error"] = f"{type(error).__name__}: {error}"[:300]
        return entry

    finished = [job for job in jobs if job.get("status") == "completed"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        entries = list(pool.map(one, finished))
    if publish_log is not None and publish_log.exists():
        text = redact(publish_log.read_text(encoding="utf-8", errors="replace"), env)
        entries.append(
            {
                "name": "publish",
                "conclusion": "in_progress",
                "key": context["log_key"],
                "url": context["url"],
                **summarize_log(text),
            }
        )
    index = {
        "generated_at": datetime.now(UTC).isoformat(),
        "run_url": context["url"],
        "listing_error": listing_error,
        "jobs": entries,
    }
    store.put_ops(context["logs_key"], json.dumps(index).encode(), "application/json")
    return context["logs_key"]


USAGE = (
    "usage: python -m cloud.ops.report upload-log <path>\n"
    "       python -m cloud.ops.report upload-dashboard"
)


def main(argv: list[str]) -> int:
    from cloud.publish.feed import r2_from_env

    if argv[:1] == ["upload-dashboard"] and len(argv) == 1:
        # Deployment step: a failure here should fail the workflow.
        print(f"dashboard uploaded: {upload_dashboard(r2_from_env())}", flush=True)
        return 0
    if argv[:1] != ["upload-log"] or len(argv) != 2:
        print(USAGE, file=sys.stderr)
        return 2
    store = r2_from_env()
    try:
        key = upload_log(store, Path(argv[1]))
        print(f"run log uploaded: {key}" if key else "no run log uploaded", flush=True)
    except Exception as error:  # never fail the workflow because of the log copy
        print(f"run log upload failed: {type(error).__name__}: {error}", flush=True)
    try:
        index = upload_job_logs(store, Path(argv[1]))
        print(f"job logs uploaded: {index}" if index else "no job logs uploaded", flush=True)
    except Exception as error:
        print(f"job log upload failed: {type(error).__name__}: {error}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
