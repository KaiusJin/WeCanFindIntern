"""Operator report: step summary, R2 ops objects, run logs and database statistics."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from scripts.cloud.ops_report import (
    HISTORY_LIMIT,
    LOG_LIMIT,
    history_row,
    publish_ops,
    redact,
    render_markdown,
    run_context,
    upload_log,
)
from scripts.cloud.ops_stats import collect_db_stats

ENV = {
    "GITHUB_RUN_ID": "123",
    "GITHUB_RUN_ATTEMPT": "2",
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_REPOSITORY": "owner/repo",
    "GITHUB_EVENT_NAME": "schedule",
    "R2_SECRET_ACCESS_KEY": "supersecretvalue",
    "DATABASE_URL": "postgresql://feed:pa55word@db.example.co:5432/postgres",
}

REPORT = {
    "completed_at": "2026-11-21T20:00:00+00:00",
    "status": "partial",
    "duration_seconds": 1800.5,
    "unique_jobs_collected": 5000,
    "database_stats": {"created": 120, "merged": 3, "updated": 40, "unchanged": 4800},
    "query_stats": {
        "indeed_succeeded": 38,
        "linkedin_failed": 2,
        "ats_boards_ok": 3000,
        "ats_rate_limited": 7,
        "stapply_jobs_kept": 2100,
    },
    "monitor": {
        "sources": {"indeed": {"jobs": 2500, "final_jobs": 2500, "simplify_recall": 0.12}},
        "coverage": {
            "reference": 100,
            "covered": 70,
            "recall": 0.7,
            "by_platform": {"workday": {"reference": 40, "covered": 20, "recall": 0.5}},
            "missed_sample": [
                {"company": "Acme", "title": "SWE Intern", "platform": "other", "url": "https://x"}
            ],
        },
    },
    "database": {
        "jobs_live": 6000,
        "jobs_new_this_run": 130,
        "jobs_updated_this_run": 900,
        "jobs_closed_this_run": 25,
        "sources": {"indeed": {"live_jobs": 3000, "seen_this_run": 2500, "new_this_run": 90}},
        "breakdowns": {"opportunity_type": {"internship": 5000, "co_op": 700, "new_grad": 300}},
    },
    "failures": ["us-swe:linkedin:LinkedInRateLimitException: 429"],
    "feed": {"version": 9, "snapshot_jobs": 4100},
    "run": {
        "url": "https://github.com/owner/repo/actions/runs/123",
        "log_key": "ops/logs/123-2.log",
    },
}


class FakeStore:
    def __init__(self, history=None):
        self.objects = {"ops/history.json": json.dumps(history).encode()} if history else {}
        self.meta = {}
        self.kept = None

    def read(self, key):
        return self.objects.get(key)

    def put_ops(self, key, body, content_type, *, content_encoding=None):
        self.objects[key] = body
        self.meta[key] = (content_type, content_encoding)

    def keep_newest(self, prefix, keep):
        self.kept = (prefix, keep)


def test_markdown_lists_sources_database_types_and_failures():
    text = render_markdown(REPORT)
    assert "Job collection: partial" in text
    assert "6000 live jobs (130 new, 900 updated, 25 closed this run)" in text
    assert "| indeed | 2500 | 2500 | 12% |" in text
    assert "| indeed | 3000 | 2500 | 90 |" in text
    assert "| co_op | 700 |" in text
    assert "| workday | 40 | 20 | 50% |" in text
    assert "LinkedInRateLimitException" in text
    assert "ops/logs/123-2.log" in text


def test_history_row_carries_database_types_and_links():
    row = history_row(REPORT)
    assert row["db_live"] == 6000 and row["db_new"] == 130 and row["db_closed"] == 25
    assert row["types"] == {"internship": 5000, "co_op": 700, "new_grad": 300}
    assert row["sources"] == {"indeed": 2500}
    assert row["run_url"].endswith("/actions/runs/123")
    assert row["log_key"] == "ops/logs/123-2.log"


def test_publish_ops_prepends_history_and_caps_it():
    store = FakeStore(history=[{"at": f"old-{i}"} for i in range(HISTORY_LIMIT)])
    publish_ops(store, REPORT)
    history = json.loads(store.objects["ops/history.json"])
    assert len(history) == HISTORY_LIMIT
    assert history[0]["status"] == "partial"
    assert json.loads(store.objects["ops/latest.json"])["status"] == "partial"
    assert store.meta["ops/index.html"][0].startswith("text/html")


def test_publish_ops_never_raises():
    class Broken(FakeStore):
        def put_ops(self, key, body, content_type, *, content_encoding=None):
            raise OSError("R2 down")

    publish_ops(Broken(), {"status": "error", "error": "boom"})


def test_run_context_from_github_env():
    context = run_context(ENV)
    assert context["url"] == "https://github.com/owner/repo/actions/runs/123"
    assert context["log_key"] == "ops/logs/123-2.log"
    assert run_context({})["log_key"] is None


def test_redact_removes_secret_values_and_url_credentials():
    text = (
        "connect postgresql://feed:pa55word@db.example.co:5432/postgres failed; "
        "key=supersecretvalue; other https://user:tok3n@host/x"
    )
    cleaned = redact(text, ENV)
    assert "pa55word" not in cleaned and "supersecretvalue" not in cleaned
    assert "tok3n" not in cleaned
    assert "[R2_SECRET_ACCESS_KEY]" in cleaned


def test_upload_log_gzips_redacts_and_prunes(tmp_path: Path):
    log = tmp_path / "run.log"
    log.write_text("start\nsecret supersecretvalue\nend\n", encoding="utf-8")
    store = FakeStore()
    key = upload_log(store, log, env=ENV)
    assert key == "ops/logs/123-2.log"
    body = gzip.decompress(store.objects[key]).decode()
    assert "supersecretvalue" not in body and body.startswith("start")
    assert store.meta[key] == ("text/plain; charset=utf-8", "gzip")
    assert store.kept == ("ops/logs/", LOG_LIMIT)
    assert upload_log(store, tmp_path / "missing.log", env=ENV) is None


class FakeDb:
    """Answers each query by the first marker it contains."""

    ANSWERS = [
        ("count(*) FILTER (WHERE j.status IN (1, 2) AND j.source_count", [(100, 90, 10, 12)]),
        ("j.created_at >= %s),", [(7, 30, 2)]),
        ("FROM job_sources s", [("indeed", 50, 40, 5), ("greenhouse", 45, 45, 2)]),
        ("unnest(j.job_subcategories)", [("backend", 20)]),
        ("WHERE j.created_at >= %s GROUP BY", [("internship", 6), ("co_op", 1)]),
        ("length(j.description)", [(3, 4, 20, 1, 2, 5)]),
        ("pg_total_relation_size", [("jobs", 50_000_000), ("job_sources", 9_000_000)]),
        ("pg_database_size", [(80_000_000,)]),
        ("GROUP BY 1", [("x", 1)]),
    ]

    def __init__(self):
        self.queries = []

    def execute(self, sql, params=()):
        self.queries.append(sql)
        for marker, rows in self.ANSWERS:
            if marker in sql:
                return _Rows(rows)
        raise AssertionError(f"unexpected query: {sql}")


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


def test_collect_db_stats_shapes_the_report():
    stats = collect_db_stats(FakeDb(), "2026-11-21T00:00:00Z")
    assert (stats["jobs_total"], stats["jobs_live"], stats["jobs_closed"]) == (100, 90, 10)
    assert stats["jobs_multi_source"] == 12
    assert (
        stats["jobs_new_this_run"],
        stats["jobs_updated_this_run"],
        stats["jobs_closed_this_run"],
    ) == (7, 30, 2)
    assert stats["sources"]["indeed"] == {"live_jobs": 50, "seen_this_run": 40, "new_this_run": 5}
    assert stats["breakdowns"]["new_by_opportunity_type"] == {"internship": 6, "co_op": 1}
    assert stats["breakdowns"]["subcategory_top"] == {"backend": 20}
    assert stats["quality"]["with_salary"] == 20
    assert stats["database_bytes"] == 80_000_000
    assert stats["table_bytes"] == {"jobs": 50_000_000, "job_sources": 9_000_000}
