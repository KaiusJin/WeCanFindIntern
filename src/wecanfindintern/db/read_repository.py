"""Read-optimized queries used by the public data API."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from wecanfindintern.api.models import (
    CollectionCheckpointResponse,
    CollectionPlanResponse,
    FacetCount,
    IngestionRunResponse,
    JobDetail,
    JobFacetsResponse,
    JobListFilters,
    JobListItem,
    JobPage,
    JobSourceResponse,
    LocationResponse,
    SalaryResponse,
    decode_cursor,
    encode_cursor,
)
from wecanfindintern.domain.classification import normalize_tag
from wecanfindintern.domain.jobs import normalize_company

RUN_STATUS = {0: "running", 1: "succeeded", 2: "partial", 3: "failed"}

JOB_SELECT = """
    j.id AS internal_id,
    j.public_id,
    j.title,
    j.company_name,
    j.location_text,
    j.city,
    j.region_code,
    j.region_name,
    j.region_type,
    j.country_code,
    j.country_name,
    j.work_mode,
    j.employment_types,
    j.opportunity_type,
    j.schedule_types,
    j.primary_schedule_type,
    j.job_category,
    j.job_subcategories,
    j.date_posted,
    j.published_sort_at,
    j.salary_interval,
    j.salary_min,
    j.salary_max,
    j.salary_currency,
    j.salary_source,
    j.salary_annual_min,
    j.salary_annual_max,
    j.skill_tags,
    j.display_tags,
    j.source_count,
    j.first_seen_at,
    j.last_seen_at
"""


class JobReadRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def list_jobs(self, filters: JobListFilters) -> JobPage:
        predicates = ["j.status = 1"]
        parameters: list[Any] = []

        if filters.cursor:
            cursor_time, cursor_id = decode_cursor(filters.cursor)
            predicates.append("(j.published_sort_at, j.id) < (%s, %s)")
            parameters.extend((cursor_time, cursor_id))
        if filters.query:
            predicates.append("j.search_document @@ websearch_to_tsquery('simple', %s)")
            parameters.append(filters.query)
        if filters.country:
            predicates.append("j.country_code = %s")
            parameters.append(filters.country)
        if filters.region:
            predicates.append("j.region_code = %s")
            parameters.append(filters.region)
        if filters.city:
            predicates.append("lower(j.city) = lower(%s)")
            parameters.append(filters.city)
        if filters.company:
            predicates.append("j.company_normalized = %s")
            parameters.append(normalize_company(filters.company))
        if filters.work_mode:
            predicates.append("j.work_mode = %s")
            parameters.append(filters.work_mode)
        if filters.employment_type:
            predicates.append("j.primary_employment_type = %s")
            parameters.append(filters.employment_type)
        if filters.opportunity_type:
            predicates.append("j.opportunity_type = %s")
            parameters.append(filters.opportunity_type)
        if filters.schedule_type:
            predicates.append("%s = ANY(j.schedule_types)")
            parameters.append(filters.schedule_type)
        if filters.category:
            predicates.append("j.job_category = %s")
            parameters.append(filters.category)
        if filters.subcategory:
            predicates.append("%s = ANY(j.job_subcategories)")
            parameters.append(filters.subcategory)
        if filters.skill:
            predicates.append("%s = ANY(j.skill_tags)")
            parameters.append(normalize_tag(filters.skill))
        if filters.posted_after:
            predicates.append("j.date_posted >= %s")
            parameters.append(filters.posted_after)
        if filters.salary_min is not None:
            predicates.append("j.salary_max >= %s")
            parameters.append(filters.salary_min)
        if filters.annual_salary_min is not None:
            predicates.append("j.salary_annual_max >= %s")
            parameters.append(filters.annual_salary_min)
        if filters.has_salary is True:
            predicates.append("j.salary_max IS NOT NULL")
        elif filters.has_salary is False:
            predicates.append("j.salary_max IS NULL")
        if filters.currency:
            predicates.append("j.salary_currency = %s")
            parameters.append(filters.currency)
        if filters.source:
            predicates.append(
                "EXISTS (SELECT 1 FROM job_sources js WHERE js.job_id = j.id AND js.source = %s)"
            )
            parameters.append(filters.source)

        parameters.append(filters.limit + 1)
        sql = f"""
            SELECT {JOB_SELECT}
            FROM jobs j
            WHERE {" AND ".join(predicates)}
            ORDER BY j.published_sort_at DESC, j.id DESC
            LIMIT %s
        """
        async with self.pool.connection() as connection:
            rows = await (await connection.execute(sql, parameters)).fetchall()

        has_more = len(rows) > filters.limit
        page_rows = rows[: filters.limit]
        items = [job_list_item(row) for row in page_rows]
        next_cursor = None
        if has_more and page_rows:
            last = page_rows[-1]
            next_cursor = encode_cursor(last["published_sort_at"], last["internal_id"])
        return JobPage(items=items, next_cursor=next_cursor, has_more=has_more)

    async def get_job(self, public_id: UUID) -> JobDetail | None:
        sql = f"""
            SELECT {JOB_SELECT},
                   j.description,
                   j.job_function,
                   j.company_industry,
                   j.company_website_url,
                   j.company_logo_url,
                   j.source_skills,
                   j.requirement_tags,
                   j.classification_version,
                   j.contact_emails,
                   j.vacancy_count
            FROM jobs j
            WHERE j.public_id = %s
        """
        async with self.pool.connection() as connection:
            row = await (await connection.execute(sql, (public_id,))).fetchone()
            if row is None:
                return None
            source_rows = await (
                await connection.execute(
                    """
                    SELECT source, source_job_id, source_url, direct_url,
                           first_seen_at, last_seen_at
                    FROM job_sources
                    WHERE job_id = %s
                    ORDER BY first_seen_at, id
                    """,
                    (row["internal_id"],),
                )
            ).fetchall()

        base = job_list_item(row).model_dump()
        return JobDetail(
            **base,
            description=row["description"],
            job_function=row["job_function"],
            company_industry=row["company_industry"],
            company_website_url=row["company_website_url"],
            company_logo_url=row["company_logo_url"],
            skills=row["source_skills"] or [],
            requirement_tags=row["requirement_tags"] or [],
            classification_version=row["classification_version"],
            contact_emails=row["contact_emails"] or [],
            vacancy_count=row["vacancy_count"],
            sources=[
                JobSourceResponse(
                    source=item["source"],
                    source_job_id=item["source_job_id"],
                    url=item["source_url"],
                    direct_url=item["direct_url"],
                    first_seen_at=item["first_seen_at"],
                    last_seen_at=item["last_seen_at"],
                )
                for item in source_rows
            ],
        )

    async def job_facets(self) -> JobFacetsResponse:
        async with self.pool.connection() as connection:
            row = await (
                await connection.execute(
                    """
                    SELECT
                        (SELECT coalesce(jsonb_agg(item ORDER BY item->>'value'), '[]')
                         FROM (SELECT jsonb_build_object('value', opportunity_type,
                                                        'count', count(*)) AS item
                               FROM jobs WHERE status = 1
                               GROUP BY opportunity_type) grouped) AS opportunity_types,
                        (SELECT coalesce(jsonb_agg(item ORDER BY item->>'value'), '[]')
                         FROM (SELECT jsonb_build_object('value', value,
                                                        'count', count(*)) AS item
                               FROM jobs, unnest(schedule_types) value
                               WHERE status = 1 GROUP BY value) grouped) AS schedule_types,
                        (SELECT coalesce(jsonb_agg(item ORDER BY item->>'value'), '[]')
                         FROM (SELECT jsonb_build_object('value', job_category,
                                                        'count', count(*)) AS item
                               FROM jobs WHERE status = 1
                               GROUP BY job_category) grouped) AS job_categories,
                        (SELECT coalesce(jsonb_agg(item ORDER BY item->>'value'), '[]')
                         FROM (SELECT jsonb_build_object('value', work_mode,
                                                        'count', count(*)) AS item
                               FROM jobs WHERE status = 1
                               GROUP BY work_mode) grouped) AS work_modes,
                        (SELECT coalesce(jsonb_agg(item ORDER BY (item->>'count')::int DESC,
                                                            item->>'value'), '[]')
                         FROM (SELECT jsonb_build_object('value', value,
                                                        'count', count(*)) AS item
                               FROM jobs, unnest(skill_tags) value
                               WHERE status = 1 GROUP BY value LIMIT 100) grouped) AS skills,
                        (SELECT coalesce(jsonb_agg(item ORDER BY item->>'value'), '[]')
                         FROM (SELECT jsonb_build_object('value', country_code,
                                                        'count', count(*)) AS item
                               FROM jobs WHERE status = 1 AND country_code IS NOT NULL
                               GROUP BY country_code) grouped) AS countries,
                        (SELECT coalesce(jsonb_agg(item ORDER BY item->>'value'), '[]')
                         FROM (SELECT jsonb_build_object('value', region_code,
                                                        'count', count(*)) AS item
                               FROM jobs WHERE status = 1 AND region_code IS NOT NULL
                               GROUP BY region_code) grouped) AS regions,
                        (SELECT coalesce(jsonb_agg(item ORDER BY (item->>'count')::int DESC,
                                                            item->>'value'), '[]')
                         FROM (SELECT jsonb_build_object('value', city,
                                                        'count', count(*)) AS item
                               FROM jobs WHERE status = 1 AND city IS NOT NULL
                               GROUP BY city LIMIT 200) grouped) AS cities,
                        (SELECT coalesce(jsonb_agg(item ORDER BY (item->>'count')::int DESC,
                                                            item->>'value'), '[]')
                         FROM (SELECT jsonb_build_object('value', company_name,
                                                        'count', count(*)) AS item
                               FROM jobs WHERE status = 1 AND company_name IS NOT NULL
                               GROUP BY company_name LIMIT 100) grouped) AS companies
                    """
                )
            ).fetchone()
        return JobFacetsResponse(
            **{
                key: [FacetCount.model_validate(item) for item in row[key]]
                for key in (
                    "opportunity_types",
                    "schedule_types",
                    "job_categories",
                    "work_modes",
                    "skills",
                    "countries",
                    "regions",
                    "cities",
                    "companies",
                )
            }
        )

    async def get_ingestion_run(self, public_id: UUID) -> IngestionRunResponse | None:
        async with self.pool.connection() as connection:
            row = await (
                await connection.execute(
                    """
                    SELECT public_id, provider, sources, query, status, started_at, finished_at,
                           fetched_count, created_count, merged_count, unchanged_count,
                           failed_count, error_summary
                    FROM ingestion_runs
                    WHERE public_id = %s
                    """,
                    (public_id,),
                )
            ).fetchone()
        if row is None:
            return None
        return IngestionRunResponse(
            id=row["public_id"],
            provider=row["provider"],
            sources=row["sources"],
            query=row["query"],
            status=RUN_STATUS[row["status"]],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            fetched_count=row["fetched_count"],
            created_count=row["created_count"],
            merged_count=row["merged_count"],
            unchanged_count=row["unchanged_count"],
            failed_count=row["failed_count"],
            error_summary=row["error_summary"],
        )

    async def list_collection_plans(self) -> list[CollectionPlanResponse]:
        async with self.pool.connection() as connection:
            rows = await (
                await connection.execute(
                    """
                    SELECT p.public_id, p.name, p.enabled, p.sites,
                           p.interval_seconds, p.next_run_at, p.last_started_at,
                           p.last_completed_at, r.public_id AS active_run_public_id,
                           coalesce(
                               jsonb_agg(
                                   jsonb_build_object(
                                       'source', c.source,
                                       'status', c.status,
                                       'offset', c.offset_value,
                                       'attempts', c.attempts,
                                       'pages_completed', c.pages_completed,
                                       'records_seen', c.records_seen,
                                       'next_retry_at', c.next_retry_at,
                                       'last_error', c.last_error
                                   ) ORDER BY c.source
                               ) FILTER (WHERE c.source IS NOT NULL),
                               '[]'::jsonb
                           ) AS checkpoints
                    FROM collection_plans p
                    LEFT JOIN ingestion_runs r ON r.id = p.active_run_id
                    LEFT JOIN collection_checkpoints c ON c.plan_id = p.id
                    GROUP BY p.id, r.public_id
                    ORDER BY p.name
                    """
                )
            ).fetchall()
        status_names = {
            0: "idle",
            1: "running",
            2: "retry_wait",
            3: "succeeded",
            4: "exhausted",
        }
        return [
            CollectionPlanResponse(
                id=row["public_id"],
                name=row["name"],
                enabled=row["enabled"],
                sites=row["sites"],
                interval_seconds=row["interval_seconds"],
                next_run_at=row["next_run_at"],
                last_started_at=row["last_started_at"],
                last_completed_at=row["last_completed_at"],
                active_run_id=row["active_run_public_id"],
                checkpoints=[
                    CollectionCheckpointResponse(
                        source=item["source"],
                        status=status_names[item["status"]],
                        offset=item["offset"],
                        attempts=item["attempts"],
                        pages_completed=item["pages_completed"],
                        records_seen=item["records_seen"],
                        next_retry_at=item["next_retry_at"],
                        last_error=item["last_error"],
                    )
                    for item in row["checkpoints"]
                ],
            )
            for row in rows
        ]


def job_list_item(row: dict[str, Any]) -> JobListItem:
    salary = None
    if any(row[key] is not None for key in ("salary_interval", "salary_min", "salary_max")):
        salary = SalaryResponse(
            interval=row["salary_interval"],
            minimum=row["salary_min"],
            maximum=row["salary_max"],
            currency=row["salary_currency"],
            source=row["salary_source"],
            annualized_minimum=row["salary_annual_min"],
            annualized_maximum=row["salary_annual_max"],
        )
    return JobListItem(
        id=row["public_id"],
        title=row["title"],
        company_name=row["company_name"],
        location=LocationResponse(
            text=row["location_text"],
            display_name=location_display_name(row),
            city=row["city"],
            region=row["region_code"],
            region_code=row["region_code"],
            region_name=row["region_name"],
            region_type=row["region_type"],
            country=row["country_code"],
            country_code=row["country_code"],
            country_name=row["country_name"],
        ),
        work_mode=row["work_mode"],
        employment_types=row["employment_types"] or [],
        opportunity_type=row["opportunity_type"],
        schedule_types=row["schedule_types"] or [],
        primary_schedule_type=row["primary_schedule_type"],
        job_category=row["job_category"],
        job_subcategories=row["job_subcategories"] or [],
        date_posted=row["date_posted"],
        published_at=row["published_sort_at"],
        salary=salary,
        skill_tags=row["skill_tags"] or [],
        display_tags=row["display_tags"] or [],
        source_count=row["source_count"],
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
    )


def location_display_name(row: dict[str, Any]) -> str | None:
    if row["location_text"]:
        return row["location_text"]
    parts = [row["city"], row["region_code"], row["country_code"]]
    display = ", ".join(str(part) for part in parts if part)
    return display or None
