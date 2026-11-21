"""Post-deduplication salary enrichment persistence."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from psycopg_pool import AsyncConnectionPool

from wecanfindintern.db.repositories.jobs import hex_bytes
from wecanfindintern.domain.jobs import SalaryRange


@dataclass(frozen=True, slots=True)
class SalaryEnrichmentCandidate:
    job_id: int
    title: str
    description: str | None
    description_hash: str | None
    country_code: str | None
    source_fingerprints: list[str]
    checked_description_hash: str | None
    enrichment_status: str | None
    salary_labels: tuple[str, ...] = ()
    input_hash: str | None = None
    row_version: str | None = None


class SalaryRepository:
    """Query and persist label and JD salaries for deduplicated jobs."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def salary_enrichment_candidates(
        self,
        source_fingerprints: Iterable[str],
    ) -> list[SalaryEnrichmentCandidate]:
        """Resolve deduplicated jobs that still need salary enrichment."""

        fingerprints = sorted(set(source_fingerprints))
        if not fingerprints:
            return []
        binary_fingerprints = [bytes.fromhex(value) for value in fingerprints]
        async with self.pool.connection() as connection:
            rows = await (
                await connection.execute(
                    """
                    WITH affected AS (
                        SELECT job_id, array_agg(encode(source_fingerprint, 'hex')) AS fingerprints
                        FROM job_sources
                        WHERE source_fingerprint = ANY(%s::bytea[])
                        GROUP BY job_id
                    )
                    SELECT j.id, j.title, j.description, j.xmin::text AS row_version,
                           encode(j.description_hash, 'hex') AS description_hash,
                           j.country_code, card.labels, encode(inputs.hash, 'hex') AS input_hash,
                           encode(j.salary_enrichment_input_hash, 'hex')
                               AS checked_description_hash,
                           j.salary_enrichment_status, affected.fingerprints AS source_fingerprints
                    FROM affected
                    JOIN jobs j ON j.id = affected.job_id
                    CROSS JOIN LATERAL (
                        SELECT coalesce(
                            array_agg(latest.label ORDER BY latest.scraped_at DESC, s.id)
                                FILTER (WHERE latest.label IS NOT NULL), ARRAY[]::text[]
                        ) AS labels
                        FROM job_sources s
                        CROSS JOIN LATERAL (
                            SELECT nullif(r.payload->>'salary_label', '') AS label, r.scraped_at
                            FROM raw_job_snapshots r
                            WHERE r.job_source_id = s.id AND r.payload_hash = s.last_payload_hash
                            ORDER BY r.scraped_at DESC, r.id DESC LIMIT 1
                        ) latest
                        WHERE s.job_id = j.id AND s.source = 'linkedin'
                    ) card
                    CROSS JOIN LATERAL (
                        SELECT CASE WHEN cardinality(card.labels) = 0 THEN j.description_hash
                            ELSE digest(jsonb_build_array(
                                encode(j.description_hash, 'hex'), j.country_code, card.labels
                            )::text, 'sha256') END AS hash
                    ) inputs
                    WHERE ((j.salary_interval IS NULL AND j.salary_min IS NULL
                            AND j.salary_max IS NULL)
                           OR j.salary_source IN ('description', 'job_description',
                                                  'linkedin_salary_label'))
                      AND (inputs.hash IS NOT NULL OR j.salary_source IS NOT NULL)
                      AND (j.salary_enrichment_input_hash IS DISTINCT FROM inputs.hash
                           OR j.salary_enrichment_status IS DISTINCT FROM CASE
                               WHEN j.salary_min IS NULL AND j.salary_max IS NULL THEN 'not_found'
                               ELSE 'complete' END)
                    ORDER BY j.id
                    """,
                    (binary_fingerprints,),
                )
            ).fetchall()
        return [
            SalaryEnrichmentCandidate(
                job_id=row["id"],
                title=row["title"],
                description=row["description"],
                description_hash=row["description_hash"],
                country_code=row["country_code"],
                source_fingerprints=row["source_fingerprints"],
                checked_description_hash=row["checked_description_hash"],
                enrichment_status=row["salary_enrichment_status"],
                salary_labels=tuple(row["labels"]),
                input_hash=row["input_hash"],
                row_version=row["row_version"],
            )
            for row in rows
        ]

    async def persist_enriched_salary(
        self,
        *,
        job_id: int,
        description_hash: str | None,
        salary: SalaryRange,
        input_hash: str | None = None,
        row_version: str | None = None,
    ) -> bool:
        return await self._save_result(
            job_id, description_hash, salary, "complete", input_hash, row_version
        )

    async def persist_enrichment_check(
        self,
        *,
        job_id: int,
        description_hash: str | None,
        status: str,
        input_hash: str | None = None,
        row_version: str | None = None,
    ) -> bool:
        return await self._save_result(
            job_id, description_hash, None, status, input_hash, row_version
        )

    async def _save_result(
        self,
        job_id: int,
        description_hash: str | None,
        salary: SalaryRange | None,
        status: str,
        input_hash: str | None,
        row_version: str | None,
    ) -> bool:
        # Ingestion updates the canonical row in the same transaction as each source snapshot.
        # Its row version protects the label as well as the optional JD from concurrent changes.
        async with self.pool.connection() as connection:
            result = await connection.execute(
                """
                UPDATE jobs
                SET salary_interval = %s, salary_min = %s, salary_max = %s,
                    salary_currency = %s, salary_source = %s,
                    salary_annual_min = %s, salary_annual_max = %s,
                    salary_enrichment_input_hash = %s,
                    salary_enrichment_status = %s,
                    salary_enrichment_checked_at = now(),
                    salary_enrichment_model = NULL,
                    updated_at = now()
                WHERE id = %s
                  AND description_hash IS NOT DISTINCT FROM %s
                  AND (xmin::text = %s OR (%s::text IS NULL AND salary_interval IS NULL
                                           AND salary_min IS NULL AND salary_max IS NULL))
                """,
                (
                    salary.interval if salary else None,
                    salary.minimum if salary else None,
                    salary.maximum if salary else None,
                    salary.currency if salary else None,
                    salary.source if salary else None,
                    salary.annualized_minimum if salary else None,
                    salary.annualized_maximum if salary else None,
                    hex_bytes(input_hash or description_hash),
                    status,
                    job_id,
                    hex_bytes(description_hash),
                    row_version,
                    row_version,
                ),
            )
        return result.rowcount == 1
