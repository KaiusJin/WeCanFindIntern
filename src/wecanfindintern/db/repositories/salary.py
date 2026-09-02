"""Post-deduplication salary enrichment persistence."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from psycopg_pool import AsyncConnectionPool

from wecanfindintern.domain.jobs import SalaryRange


@dataclass(frozen=True, slots=True)
class SalaryEnrichmentCandidate:
    job_id: int
    title: str
    description: str
    description_hash: str
    country_code: str | None
    source_fingerprints: list[str]
    checked_description_hash: str | None
    enrichment_status: str | None


class SalaryRepository:
    """Query and persist LLM/regex salary results for deduplicated jobs."""

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
                    SELECT j.id, j.title, j.description,
                           encode(j.description_hash, 'hex') AS description_hash,
                           j.country_code,
                           encode(j.salary_enrichment_input_hash, 'hex')
                               AS checked_description_hash,
                           j.salary_enrichment_status,
                           array_agg(encode(js.source_fingerprint, 'hex'))
                               AS source_fingerprints
                    FROM job_sources js
                    JOIN jobs j ON j.id = js.job_id
                    WHERE js.source_fingerprint = ANY(%s::bytea[])
                      AND j.description IS NOT NULL
                      AND j.description_hash IS NOT NULL
                      AND j.salary_interval IS NULL
                      AND j.salary_min IS NULL
                      AND j.salary_max IS NULL
                      AND (
                          j.salary_enrichment_input_hash IS DISTINCT FROM j.description_hash
                          OR j.salary_enrichment_status IS NULL
                          OR j.salary_enrichment_status NOT IN ('complete', 'not_found')
                      )
                    GROUP BY j.id
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
            )
            for row in rows
        ]

    async def persist_enriched_salary(
        self,
        *,
        job_id: int,
        description_hash: str,
        salary: SalaryRange,
        model: str | None = None,
    ) -> bool:
        """Persist an LLM salary only if the deduplicated job JD is unchanged."""

        async with self.pool.connection() as connection:
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
                    salary_enrichment_input_hash = %s,
                    salary_enrichment_status = 'complete',
                    salary_enrichment_checked_at = now(),
                    salary_enrichment_model = %s,
                    updated_at = now()
                WHERE id = %s
                  AND description_hash = %s
                  AND salary_interval IS NULL
                  AND salary_min IS NULL
                  AND salary_max IS NULL
                """,
                (
                    salary.interval,
                    salary.minimum,
                    salary.maximum,
                    salary.currency,
                    salary.source,
                    salary.annualized_minimum,
                    salary.annualized_maximum,
                    bytes.fromhex(description_hash),
                    model,
                    job_id,
                    bytes.fromhex(description_hash),
                ),
            )
        return result.rowcount == 1

    async def persist_enrichment_check(
        self,
        *,
        job_id: int,
        description_hash: str,
        status: str,
        model: str | None = None,
    ) -> bool:
        """Cache a definitive miss; errors are recorded but remain retryable."""

        async with self.pool.connection() as connection:
            result = await connection.execute(
                """
                UPDATE jobs
                SET salary_enrichment_input_hash = %s,
                    salary_enrichment_status = %s,
                    salary_enrichment_checked_at = now(),
                    salary_enrichment_model = %s,
                    updated_at = now()
                WHERE id = %s
                  AND description_hash = %s
                  AND salary_interval IS NULL
                  AND salary_min IS NULL
                  AND salary_max IS NULL
                """,
                (
                    bytes.fromhex(description_hash),
                    status,
                    model,
                    job_id,
                    bytes.fromhex(description_hash),
                ),
            )
        return result.rowcount == 1
