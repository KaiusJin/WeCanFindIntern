"""Shared SQL projection and row adapter for canonical public jobs."""

from __future__ import annotations

from typing import Any

from wecanfindintern.application.job_models import (
    JobListItem,
    LocationResponse,
    RecruitingTermResponse,
)
from wecanfindintern.application.salary_projection import salary_response

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
    j.recruiting_season,
    j.recruiting_year,
    j.skill_tags,
    j.display_tags,
    j.source_count,
    j.first_seen_at,
    j.last_seen_at
"""


def job_list_item(row: dict[str, Any]) -> JobListItem:
    salary = None
    if any(
        row[key] is not None
        for key in (
            "salary_interval",
            "salary_min",
            "salary_max",
            "salary_annual_min",
            "salary_annual_max",
        )
    ):
        salary = salary_response(
            interval=row["salary_interval"],
            minimum=row["salary_min"],
            maximum=row["salary_max"],
            currency=row["salary_currency"],
            source=row["salary_source"],
            annualized_minimum=row["salary_annual_min"],
            annualized_maximum=row["salary_annual_max"],
        )
    recruiting_term = None
    if row["recruiting_season"] is not None and row["recruiting_year"] is not None:
        recruiting_term = RecruitingTermResponse(
            season=row["recruiting_season"],
            year=row["recruiting_year"],
            display_name=f"{row['recruiting_season'].title()} {row['recruiting_year']}",
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
        recruiting_term=recruiting_term,
        skill_tags=row["skill_tags"] or [],
        display_tags=row["display_tags"] or [],
        source_count=row["source_count"],
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
    )


def location_display_name(row: dict[str, Any]) -> str | None:
    parts = [
        str(part)
        for part in (
            row["city"],
            row["region_name"] or row["region_code"],
            row["country_name"] or row["country_code"],
        )
        if part
    ]
    return ", ".join(parts) if parts else row["location_text"]
