"""Lease and checkpoint persistence for durable collection workers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from wecanfindintern.scheduler.models import CollectionCheckpoint, CollectionPlan


class CollectionRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def disable_plans_except(self, names: list[str]) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                UPDATE collection_plans
                SET enabled = false,
                    active_run_id = NULL,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE NOT (name = ANY(%s))
                """,
                (names,),
            )

    async def upsert_plan(self, definition: dict[str, Any]) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO collection_plans (
                    name, enabled, sites, query, interval_seconds,
                    page_size, max_results_per_source, max_attempts, next_run_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (name) DO UPDATE
                SET enabled = EXCLUDED.enabled,
                    sites = EXCLUDED.sites,
                    query = EXCLUDED.query,
                    interval_seconds = EXCLUDED.interval_seconds,
                    page_size = EXCLUDED.page_size,
                    max_results_per_source = EXCLUDED.max_results_per_source,
                    max_attempts = EXCLUDED.max_attempts,
                    updated_at = now()
                """,
                (
                    definition["name"],
                    definition.get("enabled", True),
                    definition["sites"],
                    Jsonb(definition["query"]),
                    definition.get("interval_seconds", 14_400),
                    definition.get("page_size", 25),
                    definition.get("max_results_per_source", 200),
                    definition.get("max_attempts", 5),
                ),
            )

    async def claim_due_plan(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> CollectionPlan | None:
        async with self.pool.connection() as connection, connection.transaction():
            row = await (
                await connection.execute(
                    """
                    SELECT id, name, sites, query, interval_seconds, page_size,
                           max_results_per_source, max_attempts, active_run_id
                    FROM collection_plans
                    WHERE enabled = true
                      AND next_run_at <= now()
                      AND (lease_expires_at IS NULL OR lease_expires_at < now())
                    ORDER BY next_run_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """
                )
            ).fetchone()
            if row is None:
                return None
            await connection.execute(
                """
                UPDATE collection_plans
                SET lease_owner = %s,
                    lease_expires_at = now() + make_interval(secs => %s),
                    last_started_at = now(),
                    updated_at = now()
                WHERE id = %s
                """,
                (worker_id, lease_seconds, row["id"]),
            )
        return plan_from_row(row)

    async def attach_run(self, *, plan_id: int, worker_id: str, run_id: int) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                UPDATE collection_plans
                SET active_run_id = %s, updated_at = now()
                WHERE id = %s AND lease_owner = %s
                """,
                (run_id, plan_id, worker_id),
            )

    async def initialize_checkpoints(
        self,
        *,
        plan_id: int,
        run_id: int,
        sites: list[str],
    ) -> None:
        async with self.pool.connection() as connection, connection.transaction():
            await connection.execute(
                """
                DELETE FROM collection_checkpoints
                WHERE plan_id = %s AND NOT (source = ANY(%s))
                """,
                (plan_id, sites),
            )
            for site in sites:
                await connection.execute(
                    """
                    INSERT INTO collection_checkpoints (plan_id, source, run_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (plan_id, source) DO UPDATE
                    SET run_id = EXCLUDED.run_id,
                        offset_value = CASE
                            WHEN collection_checkpoints.run_id IS DISTINCT FROM EXCLUDED.run_id
                            THEN 0 ELSE collection_checkpoints.offset_value END,
                        status = CASE
                            WHEN collection_checkpoints.run_id IS DISTINCT FROM EXCLUDED.run_id
                            THEN 0 ELSE collection_checkpoints.status END,
                        attempts = CASE
                            WHEN collection_checkpoints.run_id IS DISTINCT FROM EXCLUDED.run_id
                            THEN 0 ELSE collection_checkpoints.attempts END,
                        pages_completed = CASE
                            WHEN collection_checkpoints.run_id IS DISTINCT FROM EXCLUDED.run_id
                            THEN 0 ELSE collection_checkpoints.pages_completed END,
                        records_seen = CASE
                            WHEN collection_checkpoints.run_id IS DISTINCT FROM EXCLUDED.run_id
                            THEN 0 ELSE collection_checkpoints.records_seen END,
                        next_retry_at = CASE
                            WHEN collection_checkpoints.run_id IS DISTINCT FROM EXCLUDED.run_id
                            THEN NULL ELSE collection_checkpoints.next_retry_at END,
                        last_error = CASE
                            WHEN collection_checkpoints.run_id IS DISTINCT FROM EXCLUDED.run_id
                            THEN NULL ELSE collection_checkpoints.last_error END,
                        updated_at = now()
                    """,
                    (plan_id, site, run_id),
                )

    async def checkpoints(self, plan_id: int) -> list[CollectionCheckpoint]:
        async with self.pool.connection() as connection:
            rows = await (
                await connection.execute(
                    """
                    SELECT plan_id, source, run_id, offset_value, status, attempts,
                           pages_completed, records_seen, next_retry_at
                    FROM collection_checkpoints
                    WHERE plan_id = %s
                    ORDER BY source
                    """,
                    (plan_id,),
                )
            ).fetchall()
        return [checkpoint_from_row(row) for row in rows]

    async def mark_running(self, plan_id: int, source: str) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                UPDATE collection_checkpoints
                SET status = 1, next_retry_at = NULL, updated_at = now()
                WHERE plan_id = %s AND source = %s
                """,
                (plan_id, source),
            )

    async def save_page(
        self,
        *,
        plan_id: int,
        source: str,
        next_offset: int,
        page_size: int,
        completed: bool,
    ) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                UPDATE collection_checkpoints
                SET offset_value = %s,
                    status = %s,
                    attempts = 0,
                    pages_completed = pages_completed + 1,
                    records_seen = records_seen + %s,
                    next_retry_at = NULL,
                    last_error = NULL,
                    last_page_size = %s,
                    updated_at = now()
                WHERE plan_id = %s AND source = %s
                """,
                (next_offset, 3 if completed else 1, page_size, page_size, plan_id, source),
            )

    async def mark_empty_complete(self, plan_id: int, source: str) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                UPDATE collection_checkpoints
                SET status = 3,
                    attempts = 0,
                    next_retry_at = NULL,
                    last_error = NULL,
                    last_page_size = 0,
                    updated_at = now()
                WHERE plan_id = %s AND source = %s
                """,
                (plan_id, source),
            )

    async def mark_retry(
        self,
        *,
        plan_id: int,
        source: str,
        attempt: int,
        retry_at: datetime | None,
        exhausted: bool,
        error: str,
    ) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                UPDATE collection_checkpoints
                SET status = %s,
                    attempts = %s,
                    next_retry_at = %s,
                    last_error = %s,
                    updated_at = now()
                WHERE plan_id = %s AND source = %s
                """,
                (4 if exhausted else 2, attempt, retry_at, error[:2000], plan_id, source),
            )

    async def heartbeat(
        self,
        *,
        plan_id: int,
        worker_id: str,
        lease_seconds: int,
    ) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                UPDATE collection_plans
                SET lease_expires_at = now() + make_interval(secs => %s),
                    updated_at = now()
                WHERE id = %s AND lease_owner = %s
                """,
                (lease_seconds, plan_id, worker_id),
            )

    async def release_for_retry(
        self,
        *,
        plan_id: int,
        worker_id: str,
        retry_at: datetime,
    ) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                UPDATE collection_plans
                SET next_run_at = %s,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE id = %s AND lease_owner = %s
                """,
                (retry_at, plan_id, worker_id),
            )

    async def complete_plan(
        self,
        *,
        plan_id: int,
        worker_id: str,
        interval_seconds: int,
    ) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                UPDATE collection_plans
                SET active_run_id = NULL,
                    next_run_at = now() + make_interval(secs => %s),
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_completed_at = now(),
                    updated_at = now()
                WHERE id = %s AND lease_owner = %s
                """,
                (interval_seconds, plan_id, worker_id),
            )


def plan_from_row(row: dict[str, Any]) -> CollectionPlan:
    return CollectionPlan(
        id=row["id"],
        name=row["name"],
        sites=row["sites"],
        query=row["query"],
        interval_seconds=row["interval_seconds"],
        page_size=row["page_size"],
        max_results_per_source=row["max_results_per_source"],
        max_attempts=row["max_attempts"],
        active_run_id=row["active_run_id"],
    )


def checkpoint_from_row(row: dict[str, Any]) -> CollectionCheckpoint:
    return CollectionCheckpoint(
        plan_id=row["plan_id"],
        source=row["source"],
        run_id=row["run_id"],
        offset=row["offset_value"],
        status=row["status"],
        attempts=row["attempts"],
        pages_completed=row["pages_completed"],
        records_seen=row["records_seen"],
        next_retry_at=row["next_retry_at"],
    )
