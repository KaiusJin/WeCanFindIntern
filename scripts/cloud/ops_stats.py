"""Database statistics for the operator report (read-only, a few cheap aggregates).

``jobs.status`` 1-2 are live postings, 3-4 closed ones awaiting deletion.
"""

from __future__ import annotations

from typing import Any

LIVE = "j.status IN (1, 2)"

BREAKDOWNS = {
    # name: SQL expression over live jobs ``j``
    "opportunity_type": "j.opportunity_type",
    "job_category": "j.job_category",
    "country": "coalesce(j.country_code, '??')",
    "work_mode": "j.work_mode",
    "recruiting_term": "coalesce(j.recruiting_season || ' ' || j.recruiting_year::text, 'unknown')",
}


def _pairs(db: Any, sql: str, params: tuple = ()) -> dict[str, int]:
    return {str(key): int(count) for key, count in db.execute(sql, params).fetchall()}


def _one(db: Any, sql: str, params: tuple = ()) -> Any:
    return db.execute(sql, params).fetchone()


def collect_db_stats(db: Any, since: Any) -> dict[str, Any]:
    """Totals, this round's writes (rows touched since ``since``) and live breakdowns."""
    total, live, closed, multi_source = _one(
        db,
        f"""
        SELECT count(*), count(*) FILTER (WHERE {LIVE}), count(*) FILTER (WHERE NOT {LIVE}),
               count(*) FILTER (WHERE {LIVE} AND j.source_count > 1)
        FROM jobs j
        """,
    )
    new_jobs, updated_jobs, closed_now = _one(
        db,
        """
        SELECT count(*) FILTER (WHERE j.created_at >= %s),
               count(*) FILTER (WHERE j.updated_at >= %s AND j.created_at < %s),
               count(*) FILTER (WHERE j.closed_at >= %s)
        FROM jobs j
        """,
        (since, since, since, since),
    )
    sources: dict[str, dict[str, int]] = {}
    for source, live_jobs, seen, new in db.execute(
        f"""
        SELECT s.source,
               count(DISTINCT s.job_id) FILTER (WHERE {LIVE}),
               count(*) FILTER (WHERE s.last_seen_at >= %s),
               count(*) FILTER (WHERE s.first_seen_at >= %s)
        FROM job_sources s JOIN jobs j ON j.id = s.job_id
        GROUP BY s.source ORDER BY 2 DESC
        """,
        (since, since),
    ).fetchall():
        sources[str(source)] = {
            "live_jobs": int(live_jobs),
            "seen_this_run": int(seen),
            "new_this_run": int(new),
        }
    breakdowns = {
        name: _pairs(
            db, f"SELECT {expr}, count(*) FROM jobs j WHERE {LIVE} GROUP BY 1 ORDER BY 2 DESC"
        )
        for name, expr in BREAKDOWNS.items()
    }
    breakdowns["new_by_opportunity_type"] = _pairs(
        db,
        "SELECT j.opportunity_type, count(*) FROM jobs j "
        "WHERE j.created_at >= %s GROUP BY 1 ORDER BY 2 DESC",
        (since,),
    )
    breakdowns["subcategory_top"] = _pairs(
        db,
        f"""
        SELECT sub, count(*) FROM jobs j, unnest(j.job_subcategories) AS sub
        WHERE {LIVE} GROUP BY 1 ORDER BY 2 DESC LIMIT 20
        """,
    )
    quality = _one(
        db,
        f"""
        SELECT count(*) FILTER (WHERE j.description IS NULL OR length(j.description) < 200),
               count(*) FILTER (WHERE j.date_posted IS NULL),
               count(*) FILTER (WHERE j.salary_min IS NOT NULL OR j.salary_max IS NOT NULL),
               count(*) FILTER (WHERE j.country_code IS NULL),
               count(*) FILTER (WHERE j.job_category = 'other'),
               count(*) FILTER (WHERE j.opportunity_type = 'unknown')
        FROM jobs j WHERE {LIVE}
        """,
    )
    keys = (
        "no_description",
        "no_posted_date",
        "with_salary",
        "no_country",
        "category_other",
        "type_unknown",
    )
    return {
        "since": str(since),
        "jobs_total": int(total),
        "jobs_live": int(live),
        "jobs_closed": int(closed),
        "jobs_multi_source": int(multi_source),
        "jobs_new_this_run": int(new_jobs),
        "jobs_updated_this_run": int(updated_jobs),
        "jobs_closed_this_run": int(closed_now),
        "sources": sources,
        "breakdowns": breakdowns,
        "quality": {key: int(value) for key, value in zip(keys, quality, strict=True)},
    }
