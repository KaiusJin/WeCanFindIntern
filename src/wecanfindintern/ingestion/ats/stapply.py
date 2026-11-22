"""Backfill from the open Stapply job dataset (github.com/kalil0321/ats-scrapers, MIT).

Stapply scrapes ~90 ATS platforms and company career APIs (iCIMS, Oracle, UKG,
SmartRecruiters, Phenom, SuccessFactors, Eightfold, TikTok, ByteDance, Amazon,
Apple, Meta, ...) and publishes one Parquet snapshot about daily. DuckDB reads
only the columns and row groups the query needs, so the US/Canada internship
slice takes a few minutes instead of downloading the multi-GB file.

Greenhouse, Lever, Ashby and Workday are polled directly by this collector
(fresher, and more complete for those platforms), so their Stapply rows are not
emitted as jobs; their URLs are only used to discover boards to poll.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from wecanfindintern.domain.normalized_job import NormalizedJob, build_source_fingerprint
from wecanfindintern.ingestion.ats.boards import Board, board_from_url, merge_boards
from wecanfindintern.ingestion.ats.providers import Scope, _date, html_to_markdown
from wecanfindintern.ingestion.ats.relevance import countries_in, pick_location, title_kind

logger = logging.getLogger(__name__)

STAPPLY_PARQUET = "https://storage.stapply.ai/jobs/v1/all.parquet"
SELF_POLLED = frozenset({"greenhouse", "lever", "ashby", "workday"})
COLUMNS = (
    "ats_type",
    "ats_id",
    "uuid",
    "company",
    "title",
    "url",
    "apply_url",
    "country_iso",
    "location",
    "posted_at",
    "description",
    "employment_type",
    "is_remote",
)
# Cheap SQL prefilter; ``title_kind`` makes the real decision in Python.
_INTERN_SQL = (
    r"\b(intern|interns|internship|co-?op|coop|student|fellows?|fellowship|residency|"
    r"resident|apprentice|trainee|placement)\b"
)
_NEW_GRAD_SQL = (
    r"new[- ]?grad|new graduate|university grad|recent grad|early[- ]career|entry[- ]level|"
    r"junior|campus"
)


@dataclass
class StapplyCollection:
    jobs: list[NormalizedJob]
    boards: list[Board]
    stats: Counter[str] = field(default_factory=Counter)


def stapply_query(scope: Scope, source: str = STAPPLY_PARQUET) -> tuple[str, list[Any]]:
    pattern = _INTERN_SQL + (f"|{_NEW_GRAD_SQL}" if scope.include_new_grad else "")
    countries = sorted(scope.countries)
    placeholders = ", ".join("?" for _ in countries)
    sql = (
        f"SELECT {', '.join(COLUMNS)} FROM read_parquet(?) "
        f"WHERE country_iso IN ({placeholders}) AND NOT coalesce(is_stale, false) "
        "AND regexp_matches(lower(title), ?)"
    )
    return sql, [source, *countries, pattern]


def fetch_stapply_rows(scope: Scope, source: str = STAPPLY_PARQUET) -> list[dict[str, Any]]:
    import duckdb  # cloud-only dependency; imported lazily

    connection = duckdb.connect()
    try:
        connection.execute("INSTALL httpfs; LOAD httpfs;")
        sql, params = stapply_query(scope, source)
        cursor = connection.execute(sql, params)
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
    finally:
        connection.close()


def _employment(kind: str, value: Any) -> list[str]:
    if kind == "internship":
        return ["internship"]
    text = str(value or "").replace("_", "").replace("-", "").replace(" ", "").casefold()
    return [
        {"parttime": "parttime", "contract": "contract", "temporary": "temporary"}.get(
            text, "fulltime"
        )
    ]


def rows_to_jobs(rows: Iterable[dict[str, Any]], scope: Scope) -> StapplyCollection:
    result = StapplyCollection(jobs=[], boards=[])
    found_boards: list[Board] = []
    jobs: dict[str, NormalizedJob] = {}
    for row in rows:
        ats = str(row.get("ats_type") or "unknown").strip().lower()
        result.stats["rows"] += 1
        if ats in SELF_POLLED:
            for url in (row.get("url"), row.get("apply_url")):
                board = board_from_url(url, row.get("company"))
                if board is not None:
                    found_boards.append(board)
            result.stats["self_polled_rows"] += 1
            continue
        title = str(row.get("title") or "").strip()
        kind = title_kind(title, include_new_grad=scope.include_new_grad)
        if kind is None:
            result.stats["rejected_title"] += 1
            continue
        country = str(row.get("country_iso") or "").upper()
        location = row.get("location")
        countries = ({country} | countries_in(location)) & set(scope.countries)
        if not countries:
            result.stats["rejected_location"] += 1
            continue
        url = row.get("url") or row.get("apply_url")
        if not title or not url:
            result.stats["rejected_incomplete"] += 1
            continue
        job_id = str(row.get("ats_id") or row.get("uuid") or url)
        description = row.get("description")
        if description and "<" in description and ">" in description:
            description = html_to_markdown(description)
        job = NormalizedJob(
            source_fingerprint=build_source_fingerprint(ats, job_id, url),
            source=ats,
            source_job_id=job_id,
            source_url=url,
            direct_url=row.get("apply_url") or url,
            title=title,
            company_name=(str(row.get("company")).strip() or None) if row.get("company") else None,
            location_text=pick_location(str(location or "").split(";"), countries),
            date_posted=_date(row.get("posted_at")),
            employment_types=_employment(kind, row.get("employment_type")),
            is_remote=bool(row.get("is_remote")) or None,
            description=description or None,
            raw={
                "via": "stapply",
                "ats_type": ats,
                "stapply_uuid": row.get("uuid"),
                "ats_kind": kind,
            },
        )
        jobs[job.source_fingerprint] = job
        result.stats[f"kept_{ats}"] += 1
    result.jobs = sorted(jobs.values(), key=lambda job: job.source_fingerprint)
    result.boards = merge_boards(found_boards)
    result.stats["jobs_kept"] = len(result.jobs)
    result.stats["boards_discovered"] = len(result.boards)
    return result


def collect_stapply_jobs(
    scope: Scope,
    *,
    fetch_rows: Callable[[Scope], list[dict[str, Any]]] = fetch_stapply_rows,
) -> StapplyCollection:
    return rows_to_jobs(fetch_rows(scope), scope)
