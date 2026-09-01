#!/usr/bin/env python3
"""Recompute derived job classification, tags, and annualized salary."""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal

from wecanfindintern.config import Settings
from wecanfindintern.db.pool import Database
from wecanfindintern.domain.classification import CLASSIFICATION_VERSION, classify_job
from wecanfindintern.domain.jobs import infer_work_mode_from_text
from wecanfindintern.domain.location import parse_location
from wecanfindintern.domain.normalization import annualize_salary
from wecanfindintern.domain.salary import extract_salary_from_description
from wecanfindintern.ingestion.salary_llm import extract_salary_hybrid


async def backfill(*, batch_size: int, force: bool, refresh_salary: bool = False) -> int:
    database = Database(Settings.from_env())
    await database.open()
    updated = 0
    last_id = 0
    try:
        while True:
            async with database.pool.connection() as connection:
                rows = await (
                    await connection.execute(
                        """
                        SELECT id, title, description, employment_types, source_skills,
                               work_mode, salary_interval, salary_min, salary_max,
                               salary_currency, salary_source,
                               location_text, country_code, classification_version
                        FROM jobs
                        WHERE id > %s
                          AND (%s OR classification_version < %s)
                        ORDER BY id
                        LIMIT %s
                        """,
                        (last_id, force, CLASSIFICATION_VERSION, batch_size),
                    )
                ).fetchall()
            if not rows:
                break

            async with database.pool.connection() as connection, connection.transaction():
                for row in rows:
                    classification = classify_job(
                        title=row["title"],
                        description=row["description"],
                        employment_types=row["employment_types"] or [],
                        source_skills=row["source_skills"] or [],
                        work_mode=row["work_mode"],
                    )
                    work_mode = row["work_mode"]
                    if work_mode == "unknown":
                        inferred = infer_work_mode_from_text(row["description"])
                        if inferred:
                            work_mode = inferred.value
                            classification = classify_job(
                                title=row["title"],
                                description=row["description"],
                                employment_types=row["employment_types"] or [],
                                source_skills=row["source_skills"] or [],
                                work_mode=work_mode,
                            )
                    location = parse_location(row["location_text"])
                    salary_interval = row["salary_interval"]
                    salary_minimum = decimal_value(row["salary_min"])
                    salary_maximum = decimal_value(row["salary_max"])
                    salary_currency = row["salary_currency"]
                    salary_source = row["salary_source"]
                    should_extract_salary = refresh_salary
                    if should_extract_salary:
                        regex_salary = extract_salary_from_description(
                            row["description"],
                            country_code=location.country_code or row["country_code"],
                        )
                        extracted_salary = await asyncio.to_thread(
                            extract_salary_hybrid,
                            row["description"],
                            country_code=location.country_code or row["country_code"],
                            title=row["title"],
                            regex_result=regex_salary,
                        )
                        if extracted_salary:
                            salary_interval = extracted_salary.interval
                            salary_minimum = extracted_salary.minimum
                            salary_maximum = extracted_salary.maximum
                            salary_currency = extracted_salary.currency
                            salary_source = extracted_salary.source
                        elif salary_source == "description":
                            salary_interval = None
                            salary_minimum = None
                            salary_maximum = None
                            salary_currency = None
                            salary_source = None
                    await connection.execute(
                        """
                        UPDATE jobs
                        SET opportunity_type = %s,
                            schedule_types = %s,
                            primary_schedule_type = %s,
                            job_category = %s,
                            job_subcategories = %s,
                            skill_tags = %s,
                            requirement_tags = %s,
                            display_tags = %s,
                            work_mode = %s,
                            classification_version = %s,
                            salary_interval = %s,
                            salary_min = %s,
                            salary_max = %s,
                            salary_currency = %s,
                            salary_source = %s,
                            salary_annual_min = %s,
                            salary_annual_max = %s,
                            city = %s,
                            region_code = %s,
                            region_name = %s,
                            region_type = %s,
                            country_code = %s,
                            country_name = %s,
                            location_normalized = %s,
                            updated_at = now()
                        WHERE id = %s
                        """,
                        (
                            classification.opportunity_type.value,
                            [item.value for item in classification.schedule_types],
                            classification.primary_schedule_type.value,
                            classification.job_category.value,
                            classification.job_subcategories,
                            classification.skill_tags,
                            classification.requirement_tags,
                            classification.display_tags,
                            work_mode,
                            classification.classification_version,
                            salary_interval,
                            salary_minimum,
                            salary_maximum,
                            salary_currency,
                            salary_source,
                            annualize_salary(salary_minimum, salary_interval),
                            annualize_salary(salary_maximum, salary_interval),
                            location.city,
                            location.region_code,
                            location.region_name,
                            location.region_type,
                            location.country_code,
                            location.country_name,
                            location.normalized,
                            row["id"],
                        ),
                    )
                    updated += 1
            last_id = rows[-1]["id"]
    finally:
        await database.close()
    return updated


def decimal_value(value) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--refresh-salary",
        action="store_true",
        help="explicitly rerun salary extraction instead of reusing persisted job values",
    )
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 5_000:
        parser.error("--batch-size must be between 1 and 5000")
    updated = asyncio.run(
        backfill(
            batch_size=args.batch_size,
            force=args.force,
            refresh_salary=args.refresh_salary,
        )
    )
    print(f"已更新 {updated} 个岗位，分类规则版本 {CLASSIFICATION_VERSION}")


if __name__ == "__main__":
    main()
