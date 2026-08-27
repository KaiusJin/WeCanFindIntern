#!/usr/bin/env python3
"""Repair persisted salaries whose interval and amounts are inconsistent."""

from __future__ import annotations

import asyncio

from wecanfindintern.config import Settings
from wecanfindintern.db.pool import Database
from wecanfindintern.domain.jobs import annualize_salary
from wecanfindintern.domain.salary import extract_salary_from_description
from wecanfindintern.domain.salary_llm import extract_salary_with_deepseek

ANOMALY_PREDICATE = """
    (salary_interval = 'hourly' AND (
        least(coalesce(salary_min, salary_max), coalesce(salary_max, salary_min)) < 5
        OR greatest(coalesce(salary_min, 0), coalesce(salary_max, 0)) > 500
    ))
    OR (salary_interval = 'daily' AND (
        least(coalesce(salary_min, salary_max), coalesce(salary_max, salary_min)) < 40
        OR greatest(coalesce(salary_min, 0), coalesce(salary_max, 0)) > 5000
    ))
    OR (salary_interval = 'weekly' AND (
        least(coalesce(salary_min, salary_max), coalesce(salary_max, salary_min)) < 100
        OR greatest(coalesce(salary_min, 0), coalesce(salary_max, 0)) > 25000
    ))
    OR (salary_interval = 'monthly' AND (
        least(coalesce(salary_min, salary_max), coalesce(salary_max, salary_min)) < 500
        OR greatest(coalesce(salary_min, 0), coalesce(salary_max, 0)) > 100000
    ))
    OR (salary_interval = 'yearly' AND (
        least(coalesce(salary_min, salary_max), coalesce(salary_max, salary_min)) < 5000
        OR greatest(coalesce(salary_min, 0), coalesce(salary_max, 0)) > 2000000
    ))
    OR greatest(
        coalesce(salary_annual_min, 0), coalesce(salary_annual_max, 0)
    ) > 2000000
"""


async def repair() -> tuple[int, int, int]:
    database = Database(Settings.from_env())
    await database.open()
    regex_count = 0
    llm_count = 0
    unresolved_count = 0
    try:
        async with database.pool.connection() as connection:
            rows = await (
                await connection.execute(
                    """
                    SELECT id, title, description, description_hash, country_code
                    FROM jobs
                    WHERE description IS NOT NULL
                      AND description_hash IS NOT NULL
                      AND ({ANOMALY_PREDICATE})
                    ORDER BY id
                    """.format(ANOMALY_PREDICATE=ANOMALY_PREDICATE)
                )
            ).fetchall()

        for row in rows:
            extracted = extract_salary_from_description(
                row["description"],
                country_code=row["country_code"],
            )
            source = "regex"
            if extracted is None:
                extracted = await asyncio.to_thread(
                    extract_salary_with_deepseek,
                    row["description"],
                    country_code=row["country_code"],
                    title=row["title"],
                )
                source = "llm"
            if extracted is None:
                unresolved_count += 1
                continue

            async with database.pool.connection() as connection:
                result = await connection.execute(
                    """
                    UPDATE jobs
                    SET salary_interval = %s,
                        salary_min = %s,
                        salary_max = %s,
                        salary_currency = %s,
                        salary_source = %s,
                        salary_annual_min = %s,
                        salary_annual_max = %s,
                        updated_at = now()
                    WHERE id = %s
                      AND description_hash = %s
                      AND ({ANOMALY_PREDICATE})
                    """.format(ANOMALY_PREDICATE=ANOMALY_PREDICATE),
                    (
                        extracted.interval,
                        extracted.minimum,
                        extracted.maximum,
                        extracted.currency,
                        extracted.source,
                        annualize_salary(extracted.minimum, extracted.interval),
                        annualize_salary(extracted.maximum, extracted.interval),
                        row["id"],
                        row["description_hash"],
                    ),
                )
            if result.rowcount == 1:
                if source == "regex":
                    regex_count += 1
                else:
                    llm_count += 1
    finally:
        await database.close()
    return regex_count, llm_count, unresolved_count


def main() -> None:
    regex_count, llm_count, unresolved_count = asyncio.run(repair())
    print(
        "salary anomaly repair complete: "
        f"regex={regex_count}, deepseek={llm_count}, unresolved={unresolved_count}"
    )


if __name__ == "__main__":
    main()
