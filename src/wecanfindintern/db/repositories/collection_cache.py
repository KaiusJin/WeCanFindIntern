"""Read-through cache for expensive provider-owned job detail pages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg_pool import AsyncConnectionPool


@dataclass(frozen=True, slots=True)
class LinkedInDetailCacheEntry:
    details_fetched_at: datetime
    payload: dict[str, Any]


class CollectionCacheRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def completed_campaign_count(self, campaign_name: str) -> int:
        """Advance native-page sweeps only on success, preserving historical sweep numbers."""

        async with self.pool.connection() as connection:
            row = await (
                await connection.execute(
                    """
                    SELECT COALESCE(max((query->>'sweep_number')::bigint), 0) AS completed_count
                    FROM ingestion_runs
                    WHERE query->>'campaign' = %s
                      AND query ? 'sweep_number'
                      AND (status = 1 OR (status = 2 AND NOT query ? 'native_pages'))
                    """,
                    (campaign_name,),
                )
            ).fetchone()
        return int(row["completed_count"])

    async def linkedin_details(
        self,
        fingerprints: list[str],
    ) -> dict[str, LinkedInDetailCacheEntry]:
        unique = sorted(set(fingerprints))
        if not unique:
            return {}
        async with self.pool.connection() as connection:
            rows = await (
                await connection.execute(
                    """
                    SELECT encode(js.source_fingerprint, 'hex') AS source_fingerprint,
                           js.details_fetched_at,
                           snapshot.payload
                    FROM job_sources js
                    JOIN LATERAL (
                        SELECT raw.payload
                        FROM raw_job_snapshots raw
                        WHERE raw.job_source_id = js.id
                          AND raw.payload_hash = js.last_payload_hash
                        ORDER BY raw.scraped_at DESC, raw.id DESC
                        LIMIT 1
                    ) snapshot ON true
                    WHERE js.source = 'linkedin'
                      AND js.source_fingerprint = ANY(%s::bytea[])
                      AND js.details_fetched_at IS NOT NULL
                      AND nullif(snapshot.payload->>'description', '') IS NOT NULL
                    """,
                    ([bytes.fromhex(value) for value in unique],),
                )
            ).fetchall()
        return {
            row["source_fingerprint"]: LinkedInDetailCacheEntry(
                details_fetched_at=row["details_fetched_at"],
                payload=row["payload"],
            )
            for row in rows
        }
