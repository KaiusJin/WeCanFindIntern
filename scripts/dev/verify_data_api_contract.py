#!/usr/bin/env python3
"""Verify the public job API contract against the configured database."""

from __future__ import annotations

import asyncio

from wecanfindintern.application.job_models import JobListFilters
from wecanfindintern.config import Settings
from wecanfindintern.db.pool import Database
from wecanfindintern.db.read_repository import JobReadRepository

REMOVED_FIELDS = {
    "seniority",
    "seniority_level",
    "education_levels",
    "experience_min_years",
    "experience_max_years",
    "experience_range",
}


async def verify() -> None:
    database = Database(Settings.from_env())
    await database.open()
    try:
        repository = JobReadRepository(database.pool)
        page = await repository.list_jobs(JobListFilters(limit=1))
        if not page.items:
            raise RuntimeError("database contains no jobs")
        detail = await repository.get_job(page.items[0].id)
        if detail is None:
            raise RuntimeError("job detail lookup failed")
        facets = await repository.job_facets()

        exposed_removed_fields = REMOVED_FIELDS.intersection(detail.model_dump())
        if exposed_removed_fields:
            raise RuntimeError(f"removed fields are still exposed: {exposed_removed_fields}")
        detail_payload = detail.model_dump()
        if "source_skills" not in detail_payload or "skills" in detail_payload:
            raise RuntimeError(
                "detail skill contract must expose source_skills, not ambiguous skills"
            )

        print(
            {
                "page_schema": page.schema_version,
                "detail_schema": detail.schema_version,
                "facets_schema": facets.schema_version,
                "removed_fields_exposed": False,
            }
        )
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(verify())
