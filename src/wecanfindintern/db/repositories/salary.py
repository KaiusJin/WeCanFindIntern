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
            )
            for row in rows
        ]

    async def persist_enriched_salary(
        self,
        *,
        job_id: int,
        description_hash: str,
        salary: SalaryRange,
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
                    job_id,
                    bytes.fromhex(description_hash),
                ),
            )
        return result.rowcount == 1
