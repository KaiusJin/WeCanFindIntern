#!/usr/bin/env python3
"""Recompute derived job classification, tags, and annualized salary."""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal

from wecanfindintern.config import Settings
from wecanfindintern.db.pool import Database
from wecanfindintern.domain.classification import CLASSIFICATION_VERSION, classify_job
from wecanfindintern.domain.jobs import annualize_salary, parse_location


async def backfill(*, batch_size: int, force: bool) -> int:
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
                               location_text, classification_version
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
                    location = parse_location(row["location_text"])
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
                            classification_version = %s,
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
                            classification.classification_version,
                            annualize_salary(
                                decimal_value(row["salary_min"]), row["salary_interval"]
                            ),
                            annualize_salary(
                                decimal_value(row["salary_max"]), row["salary_interval"]
                            ),
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
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 5_000:
        parser.error("--batch-size must be between 1 and 5000")
    updated = asyncio.run(backfill(batch_size=args.batch_size, force=args.force))
    print(f"已更新 {updated} 个岗位，分类规则版本 {CLASSIFICATION_VERSION}")


if __name__ == "__main__":
    main()
