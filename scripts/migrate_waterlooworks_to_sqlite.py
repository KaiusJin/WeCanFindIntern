"""Move legacy WaterlooWorks sources from public PostgreSQL into dedicated SQLite."""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from typing import Any

import psycopg
from psycopg.rows import dict_row

from wecanfindintern.waterlooworks.config import WATERLOOWORKS_BOARDS, waterlooworks_database_path
from wecanfindintern.waterlooworks.repository import WaterlooWorksRepository


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the verified migration and public-source cleanup.",
    )
    args = parser.parse_args()
    database_url = os.environ["DATABASE_URL"]
    sqlite_path = waterlooworks_database_path()

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        legacy = _read_legacy_postings(connection)
        source_summary = _source_summary(connection)
        print(json.dumps(source_summary | {"latest_payloads": len(legacy)}, indent=2))
        if not args.apply:
            print("Dry run only. Pass --apply to migrate and clean up the public source rows.")
            return

        expected_ids = sorted({str(row["source_job_id"]) for row in legacy})
        if len(expected_ids) != source_summary["source_count"]:
            raise RuntimeError(
                "Migration stopped: not every public WaterlooWorks source has a raw payload "
                f"({len(expected_ids)} payloads for {source_summary['source_count']} sources)."
            )

        repository = WaterlooWorksRepository(sqlite_path)
        boards = [
            {"name": name, "label": name, "url": url}
            for name, url in WATERLOOWORKS_BOARDS
        ]
        run_id = repository.start_run(boards)
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        valid_boards = {name for name, _ in WATERLOOWORKS_BOARDS}
        for row in legacy:
            payload = dict(row["payload"])
            payload["id"] = str(row["source_job_id"])
            board = str(payload.get("jobBoard") or "full_cycle")
            if board not in valid_boards:
                board = "full_cycle"
            grouped[board].append(payload)

        for board_name, _ in WATERLOOWORKS_BOARDS:
            if not grouped[board_name]:
                repository.mark_board_failed(
                    run_id,
                    board_name,
                    "No payload was captured for this board in the legacy run.",
                )
                continue
            repository.mark_board_collecting(run_id, board_name)
            repository.store_board_postings(run_id, board_name, grouped[board_name])
        repository.finish_run(run_id, "Migrated from the former public ingestion run.")

        migrated_count = repository.count_source_ids(expected_ids)
        if migrated_count != len(expected_ids):
            raise RuntimeError(
                "Migration stopped before cleanup: dedicated DB contains "
                f"{migrated_count}/{len(expected_ids)} expected Job IDs."
            )

        cleanup = _cleanup_public_sources(connection)
        connection.commit()
        print(
            json.dumps(
                {
                    "sqlite_path": str(sqlite_path),
                    "migrated_job_ids": migrated_count,
                    **cleanup,
                },
                indent=2,
            )
        )


def _read_legacy_postings(connection: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    return connection.execute(
        """
        SELECT DISTINCT ON (source.source_job_id)
            source.source_job_id,
            snapshot.payload
        FROM job_sources source
        JOIN raw_job_snapshots snapshot ON snapshot.job_source_id=source.id
        WHERE source.source='waterlooworks' AND source.source_job_id IS NOT NULL
        ORDER BY source.source_job_id, snapshot.scraped_at DESC, snapshot.id DESC
        """
    ).fetchall()


def _source_summary(connection: psycopg.Connection[Any]) -> dict[str, int]:
    row = connection.execute(
        """
        SELECT
            COUNT(*)::int source_count,
            COUNT(DISTINCT source.job_id)::int affected_job_count,
            COUNT(DISTINCT source.job_id) FILTER (
                WHERE EXISTS (
                    SELECT 1 FROM job_sources other
                    WHERE other.job_id=source.job_id AND other.source <> 'waterlooworks'
                )
            )::int mixed_public_job_count
        FROM job_sources source
        WHERE source.source='waterlooworks'
        """
    ).fetchone()
    return dict(row)


def _cleanup_public_sources(connection: psycopg.Connection[Any]) -> dict[str, int]:
    connection.execute(
        """
        CREATE TEMP TABLE ww_affected_jobs ON COMMIT DROP AS
        SELECT DISTINCT job_id FROM job_sources WHERE source='waterlooworks'
        """
    )
    connection.execute(
        """
        CREATE TEMP TABLE ww_fingerprints ON COMMIT DROP AS
        SELECT DISTINCT source_fingerprint
        FROM job_sources WHERE source='waterlooworks'
        """
    )
    connection.execute(
        "DELETE FROM dedupe_decisions WHERE source_fingerprint IN "
        "(SELECT source_fingerprint FROM ww_fingerprints)"
    )
    connection.execute(
        "DELETE FROM dedupe_candidates WHERE source_fingerprint IN "
        "(SELECT source_fingerprint FROM ww_fingerprints)"
    )
    deleted_sources = connection.execute(
        "DELETE FROM job_sources WHERE source='waterlooworks' RETURNING id"
    ).fetchall()
    connection.execute(
        """
        UPDATE jobs job SET source_count=(
            SELECT COUNT(*) FROM job_sources source WHERE source.job_id=job.id
        )
        WHERE job.id IN (SELECT job_id FROM ww_affected_jobs)
        """
    )
    deleted_jobs = connection.execute(
        """
        DELETE FROM jobs job
        WHERE job.id IN (SELECT job_id FROM ww_affected_jobs)
          AND NOT EXISTS (SELECT 1 FROM job_sources source WHERE source.job_id=job.id)
        RETURNING id
        """
    ).fetchall()
    deleted_runs = connection.execute(
        "DELETE FROM ingestion_runs WHERE provider='waterlooworks' RETURNING id"
    ).fetchall()
    remaining = connection.execute(
        "SELECT COUNT(*) count FROM job_sources WHERE source='waterlooworks'"
    ).fetchone()["count"]
    if remaining:
        raise RuntimeError(f"Public cleanup verification failed: {remaining} sources remain.")
    return {
        "deleted_public_sources": len(deleted_sources),
        "deleted_public_jobs_without_other_sources": len(deleted_jobs),
        "deleted_public_ingestion_runs": len(deleted_runs),
        "remaining_public_waterlooworks_sources": remaining,
    }


if __name__ == "__main__":
    main()
