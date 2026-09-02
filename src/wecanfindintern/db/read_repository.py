"""Read-optimized queries used by the public data API."""

from __future__ import annotations

import re
import time
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from wecanfindintern.application.job_models import (
    FacetCount,
    JobDetail,
    JobFacetsResponse,
    JobListFilters,
    JobPage,
    JobSourceResponse,
    decode_cursor,
    encode_cursor,
)
from wecanfindintern.db.job_projection import (
    JOB_SELECT,
    job_list_item,
    location_display_name,  # noqa: F401 - compatibility export used by callers/tests
)
from wecanfindintern.domain.classification import normalize_tag
from wecanfindintern.domain.jobs import normalize_company

FACETS_CACHE_TTL_SECONDS = 120
_facets_cache_at = 0.0
_facets_cache_payload: JobFacetsResponse | None = None


def _combined_filter_values(values: list[str], value: str | None) -> list[str]:
    """Merge plural UI filters with the singular Agent/API compatibility field."""

    return list(dict.fromkeys([*values, *([value] if value else [])]))


class JobReadRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def list_jobs(self, filters: JobListFilters) -> JobPage:
        filter_predicates = ["j.status = 1"]
        filter_parameters: list[Any] = []

        if filters.query:
            filter_predicates.append("j.search_document @@ websearch_to_tsquery('simple', %s)")
            filter_parameters.append(filters.query)
        if filters.location:
            filter_predicates.append(
                "concat_ws(' ',j.location_text,j.city,j.region_code,j.region_name,"
                "j.country_code,j.country_name) ILIKE %s"
            )
            filter_parameters.append(f"%{filters.location}%")
        countries = _combined_filter_values(filters.countries, filters.country)
        if countries:
            filter_predicates.append("j.country_code = ANY(%s)")
            filter_parameters.append(countries)
        regions = _combined_filter_values(filters.regions, filters.region)
        if regions:
            composite_regions = [value for value in regions if "," in value]
            region_codes = [value for value in regions if "," not in value]
            if composite_regions and region_codes:
                filter_predicates.append(
                    "(concat(j.region_code, ',', j.country_code) = ANY(%s) "
                    "OR j.region_code = ANY(%s))"
                )
                filter_parameters.extend([composite_regions, region_codes])
            elif composite_regions:
                filter_predicates.append(
                    "concat(j.region_code, ',', j.country_code) = ANY(%s)"
                )
                filter_parameters.append(composite_regions)
            else:
                filter_predicates.append("j.region_code = ANY(%s)")
                filter_parameters.append(region_codes)
        cities = _combined_filter_values(filters.cities, filters.city)
        if cities:
            filter_predicates.append("lower(j.city) = ANY(%s)")
            filter_parameters.append([value.lower() for value in cities])
        if filters.company:
            filter_predicates.append("j.company_normalized = %s")
            filter_parameters.append(normalize_company(filters.company))
        work_modes = _combined_filter_values(filters.work_modes, filters.work_mode)
        if work_modes:
            filter_predicates.append("j.work_mode = ANY(%s)")
            filter_parameters.append(work_modes)
        if filters.employment_type:
            filter_predicates.append("j.primary_employment_type = %s")
            filter_parameters.append(filters.employment_type)
        opportunity_types = _combined_filter_values(
            filters.opportunity_types, filters.opportunity_type
        )
        if opportunity_types:
            filter_predicates.append("j.opportunity_type = ANY(%s)")
            filter_parameters.append(opportunity_types)
        schedule_types = _combined_filter_values(
            filters.schedule_types, filters.schedule_type
        )
        if schedule_types:
            filter_predicates.append("j.schedule_types && %s::text[]")
            filter_parameters.append(schedule_types)
        categories = _combined_filter_values(filters.categories, filters.category)
        if categories:
            filter_predicates.append("j.job_category = ANY(%s)")
            filter_parameters.append(categories)
        if filters.subcategory:
            filter_predicates.append("%s = ANY(j.job_subcategories)")
            filter_parameters.append(filters.subcategory)
        skills = _combined_filter_values(filters.skills, filters.skill)
        if skills:
            filter_predicates.append("j.skill_tags && %s::text[]")
            filter_parameters.append([normalize_tag(value) for value in skills])
        if filters.recruiting_terms:
            filter_predicates.append(
                "concat(initcap(j.recruiting_season), ' ', j.recruiting_year) = ANY(%s)"
            )
            filter_parameters.append(filters.recruiting_terms)
        if filters.season:
            filter_predicates.append("j.recruiting_season = %s")
            filter_parameters.append(filters.season)
        if filters.recruiting_year:
            filter_predicates.append("j.recruiting_year = %s")
            filter_parameters.append(filters.recruiting_year)
        if filters.has_recruiting_term is True:
            filter_predicates.append("j.recruiting_season IS NOT NULL")
        elif filters.has_recruiting_term is False:
            filter_predicates.append("j.recruiting_season IS NULL")
        if filters.posted_after:
            filter_predicates.append("j.date_posted >= %s")
            filter_parameters.append(filters.posted_after)
        if filters.salary_min is not None:
            filter_predicates.append(
                "coalesce(j.salary_max,j.salary_min,j.salary_annual_max,j.salary_annual_min) >= %s"
            )
            filter_parameters.append(filters.salary_min)
        if filters.hourly_salary_min is not None:
            filter_predicates.append("coalesce(j.salary_annual_max, j.salary_annual_min) >= %s")
            filter_parameters.append(filters.hourly_salary_min * 2080)
        if filters.hourly_salary_max is not None:
            filter_predicates.append("coalesce(j.salary_annual_min, j.salary_annual_max) <= %s")
            filter_parameters.append(filters.hourly_salary_max * 2080)
        if filters.annual_salary_min is not None:
            filter_predicates.append("coalesce(j.salary_annual_max, j.salary_annual_min) >= %s")
            filter_parameters.append(filters.annual_salary_min)
        if filters.annual_salary_max is not None:
            filter_predicates.append("coalesce(j.salary_annual_min, j.salary_annual_max) <= %s")
            filter_parameters.append(filters.annual_salary_max)
        if filters.has_salary is True:
            filter_predicates.append(
                "coalesce(j.salary_min,j.salary_max,j.salary_annual_min,"
                "j.salary_annual_max) IS NOT NULL"
            )
        elif filters.has_salary is False:
            filter_predicates.append(
                "j.salary_min IS NULL AND j.salary_max IS NULL "
                "AND j.salary_annual_min IS NULL AND j.salary_annual_max IS NULL"
            )
        if filters.currency:
            filter_predicates.append("j.salary_currency = %s")
            filter_parameters.append(filters.currency)
        if filters.source:
            filter_predicates.append(
                "EXISTS (SELECT 1 FROM job_sources js WHERE js.job_id = j.id AND js.source = %s)"
            )
            filter_parameters.append(filters.source)

        total_count_sql = f"""
            SELECT count(*) AS total,
                   (
                       SELECT GREATEST(
                           (SELECT max(finished_at) FROM ingestion_runs),
                           (SELECT max(started_at) FROM ingestion_runs),
                           (SELECT max(last_seen_at) FROM jobs),
                           (SELECT max(first_seen_at) FROM jobs)
                       )
                   ) AS last_updated_at
            FROM jobs j
            WHERE {" AND ".join(filter_predicates)}
        """

        relevance_sort = bool(filters.query and filters.sort_by_relevance)
        rank_select = "NULL::real AS search_rank"
        rank_parameters: list[Any] = []
        if relevance_sort:
            rank_select = (
                "ts_rank_cd(j.search_document, websearch_to_tsquery('simple', %s)) "
                "AS search_rank"
            )
            rank_parameters.append(filters.query)

        select_predicates = list(filter_predicates)
        select_parameters = [*rank_parameters, *filter_parameters]

        if filters.cursor:
            cursor_time, cursor_id, cursor_relevance = decode_cursor(filters.cursor)
            if relevance_sort:
                if cursor_relevance is None:
                    raise ValueError("A relevance cursor is required for ranked search")
                select_predicates.append(
                    "(ts_rank_cd(j.search_document, websearch_to_tsquery('simple', %s)), "
                    "j.published_sort_at, j.id) < (%s, %s, %s)"
                )
                select_parameters.extend(
                    (filters.query, cursor_relevance, cursor_time, cursor_id)
                )
            else:
                select_predicates.append("(j.published_sort_at, j.id) < (%s, %s)")
                select_parameters.extend((cursor_time, cursor_id))

        select_parameters.append(filters.limit + 1)
        order_by = (
            "search_rank DESC, j.published_sort_at DESC, j.id DESC"
            if relevance_sort
            else "j.published_sort_at DESC, j.id DESC"
        )
        sql = f"""
            SELECT {JOB_SELECT},{rank_select}
            FROM jobs j
            WHERE {" AND ".join(select_predicates)}
            ORDER BY {order_by}
            LIMIT %s
        """
        async with self.pool.connection() as connection:
            total_count_row = await (
                await connection.execute(total_count_sql, filter_parameters)
            ).fetchone()
            total_count = int(total_count_row["total"]) if total_count_row else 0
            last_updated_at = (
                total_count_row["last_updated_at"] if total_count_row else None
            )
            rows = await (await connection.execute(sql, select_parameters)).fetchall()

        has_more = len(rows) > filters.limit
        page_rows = rows[: filters.limit]
        items = [job_list_item(row) for row in page_rows]
        next_cursor = None
        if has_more and page_rows:
            last = page_rows[-1]
            next_cursor = encode_cursor(
                last["published_sort_at"],
                last["internal_id"],
                relevance=(
                    float(last["search_rank"]) if relevance_sort else None
                ),
            )
        return JobPage(
            items=items,
            total_count=total_count,
            last_updated_at=last_updated_at,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def jobs_library_version(self) -> str:
        """Cheap fingerprint of the active job library for cache keys."""

        async with self.pool.connection() as connection:
            row = await (
                await connection.execute(
                    """
                    SELECT count(*) AS total,
                           coalesce(max(last_seen_at)::text, '') AS newest
                    FROM jobs WHERE status = 1
                    """
                )
            ).fetchone()
        return f"{int(row['total']) if row else 0}:{row['newest'] if row else ''}"

    async def list_jobs_for_recommendation(
        self,
        *,
        skills: list[str],
        exclude_public_ids: list[UUID],
        target_roles: list[str] | None = None,
        locations: list[str] | None = None,
        work_modes: list[str] | None = None,
        opportunity_types: list[str] | None = None,
        limit: int = 120,
    ) -> list[dict[str, Any]]:
        """Recall recommendation candidates in one query.

        Matches normalized ``skill_tags`` (GIN-indexed array overlap) or the
        title/company full-text document, excludes already-tracked jobs, and
        returns the description excerpt plus one application URL inline so the
        caller never needs per-job follow-up queries.
        """

        from wecanfindintern.domain.classification import normalize_tag

        predicates = ["j.status = 1"]
        parameters: list[Any] = []
        if exclude_public_ids:
            predicates.append("j.public_id <> ALL(%s)")
            parameters.append([str(value) for value in exclude_public_ids])
        if target_roles:
            predicates.append("lower(j.title) LIKE ANY(%s)")
            parameters.append([f"%{value.lower()}%" for value in target_roles])
        if locations:
            predicates.append("lower(coalesce(j.location_text,'')) LIKE ANY(%s)")
            parameters.append([f"%{value.lower()}%" for value in locations])
        if work_modes:
            predicates.append("j.work_mode = ANY(%s)")
            parameters.append(work_modes)
        if opportunity_types:
            predicates.append("j.opportunity_type = ANY(%s)")
            parameters.append(opportunity_types)
        normalized_tags = sorted({normalize_tag(skill) for skill in skills if skill})
        tsquery = _recommendation_tsquery(skills)
        if normalized_tags or tsquery:
            predicates.append(
                "(j.skill_tags && %s::text[] "
                "OR j.search_document @@ to_tsquery('simple', %s))"
            )
            parameters.extend((normalized_tags, tsquery))
        parameters.append(limit)
        sql = f"""
            SELECT {JOB_SELECT},
                   j.requirement_tags,
                   LEFT(j.description, 4000) AS description_excerpt,
                   (
                       SELECT coalesce(js.direct_url, js.source_url)
                       FROM job_sources js
                       WHERE js.job_id = j.id
                       ORDER BY js.first_seen_at, js.id
                       LIMIT 1
                   ) AS application_url
            FROM jobs j
            WHERE {" AND ".join(predicates)}
            ORDER BY j.published_sort_at DESC, j.id DESC
            LIMIT %s
        """
        async with self.pool.connection() as connection:
            rows = await (await connection.execute(sql, parameters)).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            results.append(
                {
                    "item": job_list_item(row),
                    "description": row["description_excerpt"],
                    "url": row["application_url"],
                    "requirement_tags": row["requirement_tags"] or [],
                }
            )
        return results

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

        # JobDetail owns a newer schema contract than the list projection.
        # Exclude the list discriminator so the detail model applies v4.
        base = job_list_item(row).model_dump(exclude={"schema_version"})
        return JobDetail(
            **base,
            description=row["description"],
            job_function=row["job_function"],
            company_industry=row["company_industry"],
            company_website_url=row["company_website_url"],
            company_logo_url=row["company_logo_url"],
            source_skills=row["source_skills"] or [],
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

    async def geo_distribution(self) -> list[dict[str, Any]]:
        """Active job counts per U.S. state and Canadian province."""

        async with self.pool.connection() as connection:
            rows = await (
                await connection.execute(
                    """
                    SELECT country_code,
                           region_code,
                           coalesce(region_name, region_code) AS region_name,
                           count(*) AS job_count
                    FROM jobs
                    WHERE status = 1
                      AND country_code IN ('US', 'CA')
                      AND region_code IS NOT NULL
                    GROUP BY country_code, region_code, region_name
                    ORDER BY job_count DESC
                    """
                )
            ).fetchall()
        return [
            {
                "country": row["country_code"],
                "region_code": row["region_code"],
                "region_name": row["region_name"],
                "count": int(row["job_count"]),
            }
            for row in rows
        ]

    async def job_facets(self) -> JobFacetsResponse:
        global _facets_cache_at, _facets_cache_payload
        now = time.monotonic()
        if (
            _facets_cache_payload is not None
            and now - _facets_cache_at < FACETS_CACHE_TTL_SECONDS
        ):
            return _facets_cache_payload

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
                         FROM (SELECT jsonb_build_object(
                                                        'value', concat(
                                                            region_code, ',', country_code
                                                        ),
                                                        'count', count(*)) AS item
                               FROM jobs
                               WHERE status = 1
                                 AND region_code IS NOT NULL
                                 AND country_code IS NOT NULL
                               GROUP BY region_code, country_code) grouped) AS regions,
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
                               GROUP BY company_name LIMIT 100) grouped) AS companies,
                        (SELECT coalesce(jsonb_agg(item ORDER BY (item->>'count')::int DESC,
                                                            item->>'value'), '[]')
                         FROM (
                             SELECT jsonb_build_object(
                                 'value',
                                 concat(initcap(recruiting_season), ' ', recruiting_year),
                                 'count', count(*)
                             ) AS item
                             FROM jobs
                             WHERE status = 1
                               AND recruiting_season IS NOT NULL
                               AND recruiting_year IS NOT NULL
                             GROUP BY recruiting_season, recruiting_year
                         ) grouped) AS recruiting_terms,
                        (SELECT coalesce(jsonb_agg(item ORDER BY (item->>'count')::int DESC,
                                                            item->>'value'), '[]')
                         FROM (SELECT jsonb_build_object('value', recruiting_season,
                                                        'count', count(*)) AS item
                               FROM jobs WHERE status = 1 AND recruiting_season IS NOT NULL
                               GROUP BY recruiting_season) grouped) AS recruiting_seasons,
                        (
                            SELECT GREATEST(
                                (SELECT max(finished_at) FROM ingestion_runs),
                                (SELECT max(started_at) FROM ingestion_runs),
                                (SELECT max(last_seen_at) FROM jobs),
                                (SELECT max(first_seen_at) FROM jobs)
                            )
                        ) AS last_updated_at
                    """
                )
            ).fetchone()
        response = JobFacetsResponse(
            last_updated_at=row["last_updated_at"] if row else None,
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
                    "recruiting_terms",
                    "recruiting_seasons",
                )
            }
        )
        _facets_cache_at = now
        _facets_cache_payload = response
        return response

def _recommendation_tsquery(skills: list[str], max_phrases: int = 20) -> str:
    """Build a safe OR-of-AND tsquery from free-text profile skills.

    ``to_tsquery`` treats ``& | ! ( ) : *`` as operators, and skills like
    ``c++`` or ``c#`` cannot round-trip through lexemes. Strip every skill to
    alphanumeric tokens; skills without tokens are skipped and rely on the
    ``skill_tags`` array overlap instead.
    """

    phrases: list[str] = []
    for skill in skills[:max_phrases]:
        tokens = re.findall(r"[a-z0-9]+", skill.lower())
        if tokens:
            phrases.append(" & ".join(tokens))
    return " | ".join(phrases)
