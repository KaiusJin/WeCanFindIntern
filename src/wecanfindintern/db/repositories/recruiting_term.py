"""Recruiting season extraction persistence for deduplicated jobs."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from psycopg_pool import AsyncConnectionPool

from wecanfindintern.domain.recruiting_term import RecruitingTerm


@dataclass(frozen=True, slots=True)
class RecruitingTermCandidate:
    job_id: int
    title: str
    description: str | None
    checked_input_hash: str | None


class RecruitingTermRepository:
    """Query and persist recruiting season results against content hashes."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def recruiting_term_candidates(
        self,
        source_fingerprints: Iterable[str] | None = None,
    ) -> list[RecruitingTermCandidate]:
        """Return unique active jobs, optionally limited to one campaign's sources."""

        fingerprints = sorted(set(source_fingerprints or []))
        parameters: tuple[Any, ...] = ()
        source_predicate = ""
        if fingerprints:
            source_predicate = """
                AND EXISTS (
                    SELECT 1 FROM job_sources js
                    WHERE js.job_id = j.id
                      AND js.source_fingerprint = ANY(%s::bytea[])
                )
            """
            parameters = ([bytes.fromhex(value) for value in fingerprints],)
        async with self.pool.connection() as connection:
            rows = await (
                await connection.execute(
                    f"""
                    SELECT j.id, j.title, j.description,
                           encode(j.recruiting_term_input_hash, 'hex') AS checked_input_hash
                    FROM jobs j
                    WHERE j.status = 1
                    {source_predicate}
                    ORDER BY j.id
                    """,
                    parameters,
                )
            ).fetchall()
        return [
            RecruitingTermCandidate(
                job_id=row["id"],
                title=row["title"],
                description=row["description"],
                checked_input_hash=row["checked_input_hash"],
            )
            for row in rows
        ]

    async def persist_recruiting_term(
        self,
        *,
        job_id: int,
        input_hash: str,
        term: RecruitingTerm | None,
    ) -> bool:
        """Persist positive or negative extraction against an exact content hash."""

        async with self.pool.connection() as connection:
            result = await connection.execute(
                """
                UPDATE jobs
                SET recruiting_season = %s,
                    recruiting_year = %s,
                    recruiting_term_source = %s,
                    recruiting_term_evidence = %s,
                    recruiting_term_input_hash = %s,
                    recruiting_term_checked_at = now(),
                    recruiting_term_model = NULL,
                    updated_at = now()
                WHERE id = %s
                  AND digest(title || E'\\n' || coalesce(description, ''), 'sha256') = %s
                """,
                (
                    term.season if term else None,
                    term.year if term else None,
                    term.source if term else "not_found",
                    term.evidence if term else None,
                    bytes.fromhex(input_hash),
                    job_id,
                    bytes.fromhex(input_hash),
                ),
            )
        return result.rowcount == 1
