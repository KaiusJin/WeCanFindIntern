#!/usr/bin/env python3
"""Re-parse job location_text and repair the cleaned hierarchy columns.

Re-runs ``parse_location`` for every job with a location and updates
city/region_code/region_name/region_type/country_code/country_name whenever
the parsed result differs from what is stored. Fixes historical rows that were
ingested before parser fixes (e.g. ``region_code='REMOTE'``, area phrases in
the region slot, missing country names).
"""

from __future__ import annotations

import argparse

import psycopg
from psycopg.rows import dict_row

from wecanfindintern.config import Settings
from wecanfindintern.domain.location import parse_location

LOCATION_COLUMNS = (
    "city",
    "region_code",
    "region_name",
    "region_type",
    "country_code",
    "country_name",
)


def _desired(location_text: str) -> dict[str, str | None]:
    location = parse_location(location_text)
    return {column: getattr(location, column) for column in LOCATION_COLUMNS}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing.",
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    connection_info = settings.database_url

    changed = 0
    unchanged = 0
    with psycopg.connect(connection_info, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT id, location_text, city, region_code, region_name, region_type,
                   country_code, country_name
            FROM jobs
            WHERE location_text IS NOT NULL AND location_text <> ''
            """
        ).fetchall()
        updates: list[tuple[dict, int]] = []
        for row in rows:
            desired = _desired(row["location_text"])
            current = {column: row[column] for column in LOCATION_COLUMNS}
            if desired != current:
                updates.append((desired, row["id"]))
            else:
                unchanged += 1
        if args.dry_run:
            print(f"{len(rows)} rows scanned; {len(updates)} would change.")
            for desired, job_id in updates[:20]:
                print(f"  job {job_id}: {desired}")
            return
        for desired, job_id in updates:
            connection.execute(
                """
                UPDATE jobs
                SET city = %s, region_code = %s, region_name = %s, region_type = %s,
                    country_code = %s, country_name = %s
                WHERE id = %s
                """,
                (
                    desired["city"],
                    desired["region_code"],
                    desired["region_name"],
                    desired["region_type"],
                    desired["country_code"],
                    desired["country_name"],
                    job_id,
                ),
            )
            changed += 1
        connection.commit()
    print(f"{len(rows)} rows scanned; {changed} updated; {unchanged} already correct.")


if __name__ == "__main__":
    main()
