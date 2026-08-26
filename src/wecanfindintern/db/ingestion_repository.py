"""Transactional, idempotent ingestion with indexed dedupe candidate lookup."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from wecanfindintern.deduplication import (
    CandidateJob,
    DedupeAction,
    choose_duplicate,
)
from wecanfindintern.domain.jobs import CanonicalJobInput, SalaryRange


class IngestionOutcome(StrEnum):
    CREATED = "created"
    MERGED = "merged"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class IngestionRun:
    internal_id: int
    public_id: UUID


@dataclass(frozen=True, slots=True)
class SalaryEnrichmentCandidate:
    job_id: int
    title: str
    description: str
    description_hash: str
    country_code: str | None
    source_fingerprints: list[str]


class JobIngestionRepository:
    """Write path optimized for bounded batches, not one connection per row."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def persisted_salaries_by_source(
        self,
        source_fingerprints: Iterable[str],
    ) -> dict[str, tuple[int, str | None, SalaryRange]]:
        """Return job-ID-owned salary results for previously seen source jobs."""

        fingerprints = sorted(set(source_fingerprints))
        if not fingerprints:
            return {}
        binary_fingerprints = [bytes.fromhex(value) for value in fingerprints]
        async with self.pool.connection() as connection:
            rows = await (
                await connection.execute(
                    """
                    SELECT encode(js.source_fingerprint, 'hex') AS source_fingerprint,
                           j.id AS job_id, encode(j.description_hash, 'hex') AS description_hash,
                           j.salary_interval, j.salary_min, j.salary_max, j.salary_currency,
                           j.salary_source, j.salary_annual_min, j.salary_annual_max
                    FROM job_sources js
                    JOIN jobs j ON j.id = js.job_id
                    WHERE js.source_fingerprint = ANY(%s::bytea[])
                      AND j.salary_interval IS NOT NULL
                      AND (j.salary_min IS NOT NULL OR j.salary_max IS NOT NULL)
                    """,
                    (binary_fingerprints,),
                )
            ).fetchall()
        return {
            row["source_fingerprint"]: (
                row["job_id"],
                row["description_hash"],
                SalaryRange(
                    interval=row["salary_interval"],
                    minimum=row["salary_min"],
                    maximum=row["salary_max"],
                    currency=row["salary_currency"],
                    source=row["salary_source"],
                    annualized_minimum=row["salary_annual_min"],
                    annualized_maximum=row["salary_annual_max"],
                ),
            )
            for row in rows
        }

    async def salary_enrichment_candidates(
        self,
        source_fingerprints: Iterable[str],
    ) -> list[SalaryEnrichmentCandidate]:
        """Resolve deduplicated jobs that still need LLM salary enrichment."""

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

    async def start_run(
        self,
        *,
        sources: list[str],
        query: dict[str, Any],
        collection_plan_id: int | None = None,
    ) -> IngestionRun:
        async with self.pool.connection() as connection:
            row = await (
                await connection.execute(
                    """
                    INSERT INTO ingestion_runs (
                        provider, sources, query, collection_plan_id
                    )
                    VALUES ('jobspy', %s, %s, %s)
                    RETURNING id, public_id
                    """,
                    (sources, Jsonb(query), collection_plan_id),
                )
            ).fetchone()
        return IngestionRun(internal_id=row["id"], public_id=row["public_id"])

    async def ingest_batch(
        self,
        *,
        run_id: int,
        jobs: Iterable[CanonicalJobInput],
        scraped_at: datetime,
    ) -> Counter[str]:
        """Ingest a bounded batch in one transaction.

        Callers should use batches of roughly 100-500 jobs. Exact identities use
        unique bytea indexes; fuzzy scoring only sees at most 25 blocked candidates.
        """

        counts: Counter[str] = Counter()
        async with self.pool.connection() as connection, connection.transaction():
            await connection.execute(
                "SELECT ensure_raw_job_snapshot_partition(%s)",
                (scraped_at,),
            )
            for job in jobs:
                outcome = await self._ingest_one(
                    connection,
                    run_id=run_id,
                    incoming=job,
                    scraped_at=scraped_at,
                )
                counts[outcome.value] += 1
            await connection.execute(
                """
                UPDATE ingestion_runs
                SET fetched_count = fetched_count + %s,
                    created_count = created_count + %s,
                    merged_count = merged_count + %s,
                    unchanged_count = unchanged_count + %s
                WHERE id = %s
                """,
                (
                    sum(counts.values()),
                    counts[IngestionOutcome.CREATED.value],
                    counts[IngestionOutcome.MERGED.value],
                    counts[IngestionOutcome.UNCHANGED.value],
                    run_id,
                ),
            )
        return counts

    async def finish_run(
        self,
        run_id: int,
        *,
        failed_count: int = 0,
        error_summary: str | None = None,
        partial: bool = False,
    ) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                UPDATE ingestion_runs
                SET status = CASE
                        WHEN %s > 0 AND fetched_count = 0 THEN 3
                        WHEN %s OR %s > 0 THEN 2
                        ELSE 1
                    END,
                    finished_at = now(),
                    failed_count = failed_count + %s,
                    error_summary = %s
                WHERE id = %s
                """,
                (
                    failed_count,
                    partial,
                    failed_count,
                    failed_count,
                    error_summary,
                    run_id,
                ),
            )

    async def _ingest_one(
        self,
        connection: AsyncConnection,
        *,
        run_id: int,
        incoming: CanonicalJobInput,
        scraped_at: datetime,
    ) -> IngestionOutcome:
        fingerprint = bytes.fromhex(incoming.source.source_fingerprint)
        block_hash = bytes.fromhex(incoming.dedupe.block_key)
        direct_hash = hex_bytes(incoming.dedupe.direct_url_hash)
        payload_hash = stable_json_hash(incoming.source.payload)

        # Locks are sorted to avoid deadlocks. They serialize only matching source
        # or dedupe blocks, not the whole ingestion run.
        lock_values = [fingerprint, block_hash]
        if direct_hash:
            lock_values.append(direct_hash)
        lock_keys = sorted({advisory_key(value) for value in lock_values})
        for lock_key in lock_keys:
            await connection.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))

        existing = await (
            await connection.execute(
                """
                SELECT id, job_id, last_payload_hash
                FROM job_sources
                WHERE source_fingerprint = %s
                FOR UPDATE
                """,
                (fingerprint,),
            )
        ).fetchone()
        if existing:
            await self._refresh_existing_source(
                connection,
                incoming=incoming,
                source_id=existing["id"],
                job_id=existing["job_id"],
                payload_hash=payload_hash,
                previous_payload_hash=existing["last_payload_hash"],
                run_id=run_id,
                scraped_at=scraped_at,
            )
            return IngestionOutcome.UNCHANGED

        candidates = await self._find_candidates(connection, incoming)
        decision = choose_duplicate(incoming, candidates)
        if decision.action is DedupeAction.MERGE and decision.candidate_job_id:
            job_id = decision.candidate_job_id
            outcome = IngestionOutcome.MERGED
            await self._refresh_canonical_job(connection, job_id, incoming, scraped_at)
        else:
            job_id = await self._insert_job(connection, incoming, scraped_at)
            outcome = IngestionOutcome.CREATED

        source_id = await self._insert_source(
            connection,
            job_id=job_id,
            incoming=incoming,
            fingerprint=fingerprint,
            payload_hash=payload_hash,
            scraped_at=scraped_at,
        )
        await self._insert_snapshot(
            connection,
            run_id=run_id,
            source_id=source_id,
            fingerprint=fingerprint,
            payload_hash=payload_hash,
            payload=incoming.source.payload,
            scraped_at=scraped_at,
        )
        await connection.execute(
            """
            UPDATE jobs
            SET source_count = source_count + 1,
                last_seen_at = GREATEST(last_seen_at, %s),
                last_verified_at = GREATEST(last_verified_at, %s),
                updated_at = now()
            WHERE id = %s
            """,
            (scraped_at, scraped_at, job_id),
        )

        await connection.execute(
            """
            INSERT INTO dedupe_decisions (
                source_fingerprint, resulting_job_id, compared_job_id,
                action, score, algorithm_version, evidence
            ) VALUES (%s, %s, %s, %s, %s, 2, %s)
            """,
            (
                fingerprint,
                job_id,
                decision.candidate_job_id,
                decision.action.value,
                decision.score,
                Jsonb(decision.evidence),
            ),
        )
        return outcome

    async def _find_candidates(
        self,
        connection: AsyncConnection,
        incoming: CanonicalJobInput,
    ) -> list[CandidateJob]:
        rows_by_id: dict[int, dict[str, Any]] = {}
        direct_hash = hex_bytes(incoming.dedupe.direct_url_hash)
        if direct_hash:
            rows = await (
                await connection.execute(
                    CANDIDATE_SELECT
                    + """
                    JOIN job_sources match_source ON match_source.job_id = j.id
                    WHERE j.status = 1 AND match_source.direct_url_hash = %s
                    GROUP BY j.id
                    LIMIT 10
                    """,
                    (direct_hash,),
                )
            ).fetchall()
            rows_by_id.update((row["id"], row) for row in rows)

        blocked_rows = await (
            await connection.execute(
                CANDIDATE_SELECT
                + """
                WHERE j.status = 1
                  AND j.dedupe_block_key = %s
                  AND (
                    %s::date IS NULL OR j.date_posted IS NULL OR
                    j.date_posted BETWEEN %s::date - 60 AND %s::date + 60
                  )
                GROUP BY j.id
                ORDER BY abs(coalesce(j.date_posted, %s::date) - %s::date), j.id DESC
                LIMIT 25
                """,
                (
                    bytes.fromhex(incoming.dedupe.block_key),
                    incoming.date_posted,
                    incoming.date_posted,
                    incoming.date_posted,
                    incoming.date_posted,
                    incoming.date_posted,
                ),
            )
        ).fetchall()
        rows_by_id.update((row["id"], row) for row in blocked_rows)
        return [candidate_from_row(row) for row in rows_by_id.values()]

    async def _insert_job(
        self,
        connection: AsyncConnection,
        incoming: CanonicalJobInput,
        scraped_at: datetime,
    ) -> int:
        salary = incoming.salary
        row = await (
            await connection.execute(
                """
                INSERT INTO jobs (
                    title, title_normalized, company_name, company_normalized,
                    company_industry, company_website_url, company_logo_url,
                    location_text, city, region_code, region_name, region_type,
                    country_code, country_name, location_normalized,
                    work_mode, employment_types, primary_employment_type,
                    opportunity_type, schedule_types, primary_schedule_type,
                    job_category, job_subcategories,
                    skill_tags, requirement_tags, display_tags, classification_version,
                    date_posted, published_sort_at, job_function,
                    description, description_hash,
                    salary_interval, salary_min, salary_max, salary_currency, salary_source,
                    salary_annual_min, salary_annual_max,
                    source_skills, contact_emails, vacancy_count,
                    dedupe_block_key, first_seen_at, last_seen_at, last_verified_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s
                )
                RETURNING id
                """,
                (
                    incoming.title,
                    incoming.title_normalized,
                    incoming.company.name,
                    incoming.company.normalized_name,
                    incoming.company.industry,
                    incoming.company.website_url,
                    incoming.company.logo_url,
                    incoming.location.raw,
                    incoming.location.city,
                    incoming.location.region_code,
                    incoming.location.region_name,
                    incoming.location.region_type,
                    incoming.location.country_code,
                    incoming.location.country_name,
                    incoming.location.normalized,
                    incoming.work_mode.value,
                    incoming.employment_types,
                    incoming.primary_employment_type,
                    incoming.opportunity_type.value,
                    [item.value for item in incoming.schedule_types],
                    incoming.primary_schedule_type.value,
                    incoming.job_category.value,
                    incoming.job_subcategories,
                    incoming.skill_tags,
                    incoming.requirement_tags,
                    incoming.display_tags,
                    incoming.classification_version,
                    incoming.date_posted,
                    incoming.published_sort_at,
                    incoming.job_function,
                    incoming.description,
                    hex_bytes(incoming.dedupe.description_hash),
                    salary.interval if salary else None,
                    salary.minimum if salary else None,
                    salary.maximum if salary else None,
                    salary.currency if salary else None,
                    salary.source if salary else None,
                    salary.annualized_minimum if salary else None,
                    salary.annualized_maximum if salary else None,
                    incoming.source_skills,
                    incoming.contact_emails,
                    incoming.vacancy_count,
                    bytes.fromhex(incoming.dedupe.block_key),
                    incoming.first_seen_at,
                    scraped_at,
                    scraped_at,
                ),
            )
        ).fetchone()
        await connection.execute(
            """
            INSERT INTO job_status_history (job_id, to_status, reason)
            VALUES (%s, 1, 'first_seen')
            """,
            (row["id"],),
        )
        return row["id"]

    async def _insert_source(
        self,
        connection: AsyncConnection,
        *,
        job_id: int,
        incoming: CanonicalJobInput,
        fingerprint: bytes,
        payload_hash: bytes,
        scraped_at: datetime,
    ) -> int:
        row = await (
            await connection.execute(
                """
                INSERT INTO job_sources (
                    job_id, source, source_job_id, source_url, canonical_source_url,
                    direct_url, canonical_direct_url, direct_url_hash,
                    source_fingerprint, last_payload_hash,
                    first_seen_at, last_seen_at, last_scraped_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    job_id,
                    incoming.source.source,
                    incoming.source.source_job_id,
                    incoming.source.source_url,
                    incoming.source.canonical_source_url,
                    incoming.source.direct_url,
                    incoming.source.canonical_direct_url,
                    hex_bytes(incoming.dedupe.direct_url_hash),
                    fingerprint,
                    payload_hash,
                    incoming.first_seen_at,
                    scraped_at,
                    scraped_at,
                ),
            )
        ).fetchone()
        return row["id"]

    async def _refresh_existing_source(
        self,
        connection: AsyncConnection,
        *,
        incoming: CanonicalJobInput,
        source_id: int,
        job_id: int,
        payload_hash: bytes,
        previous_payload_hash: bytes,
        run_id: int,
        scraped_at: datetime,
    ) -> None:
        changed = payload_hash != previous_payload_hash
        await connection.execute(
            """
            UPDATE job_sources
            SET source_url = %s,
                canonical_source_url = %s,
                direct_url = %s,
                canonical_direct_url = %s,
                direct_url_hash = %s,
                last_payload_hash = %s,
                last_seen_at = GREATEST(last_seen_at, %s),
                last_scraped_at = %s,
                updated_at = now()
            WHERE id = %s
            """,
            (
                incoming.source.source_url,
                incoming.source.canonical_source_url,
                incoming.source.direct_url,
                incoming.source.canonical_direct_url,
                hex_bytes(incoming.dedupe.direct_url_hash),
                payload_hash,
                scraped_at,
                scraped_at,
                source_id,
            ),
        )
        await self._refresh_canonical_job(connection, job_id, incoming, scraped_at)
        if changed:
            await self._insert_snapshot(
                connection,
                run_id=run_id,
                source_id=source_id,
                fingerprint=bytes.fromhex(incoming.source.source_fingerprint),
                payload_hash=payload_hash,
                payload=incoming.source.payload,
                scraped_at=scraped_at,
            )

    async def _refresh_canonical_job(
        self,
        connection: AsyncConnection,
        job_id: int,
        incoming: CanonicalJobInput,
        scraped_at: datetime,
    ) -> None:
        await connection.execute(
            """
            UPDATE jobs
            SET description = CASE
                    WHEN length(coalesce(%s, '')) > length(coalesce(description, '')) THEN %s
                    ELSE description
                END,
                description_hash = CASE
                    WHEN length(coalesce(%s, '')) > length(coalesce(description, '')) THEN %s
                    ELSE description_hash
                END,
                city = coalesce(city, %s),
                region_code = coalesce(region_code, %s),
                region_name = coalesce(region_name, %s),
                region_type = coalesce(region_type, %s),
                country_code = coalesce(country_code, %s),
                country_name = coalesce(country_name, %s),
                location_normalized = CASE
                    WHEN location_normalized = '' THEN %s ELSE location_normalized
                END,
                work_mode = CASE WHEN work_mode = 'unknown' THEN %s ELSE work_mode END,
                employment_types = ARRAY(
                    SELECT DISTINCT value
                    FROM unnest(employment_types || %s::text[]) AS value
                    ORDER BY value
                ),
                primary_employment_type = coalesce(primary_employment_type, %s),
                opportunity_type = CASE
                    WHEN %s <> 'unknown' THEN %s ELSE opportunity_type
                END,
                schedule_types = ARRAY(
                    SELECT DISTINCT value
                    FROM unnest(schedule_types || %s::text[]) AS value
                    ORDER BY value
                ),
                primary_schedule_type = CASE
                    WHEN %s <> 'unknown' THEN %s ELSE primary_schedule_type
                END,
                job_category = CASE
                    WHEN job_category = 'other' AND %s <> 'other' THEN %s
                    ELSE job_category
                END,
                job_subcategories = ARRAY(
                    SELECT value
                    FROM unnest(job_subcategories || %s::text[])
                        WITH ORDINALITY AS item(value, position)
                    GROUP BY value
                    ORDER BY min(position)
                ),
                skill_tags = ARRAY(
                    SELECT value
                    FROM unnest(skill_tags || %s::text[])
                        WITH ORDINALITY AS item(value, position)
                    GROUP BY value
                    ORDER BY min(position)
                ),
                requirement_tags = ARRAY(
                    SELECT DISTINCT value
                    FROM unnest(requirement_tags || %s::text[]) AS value
                    ORDER BY value
                ),
                display_tags = ARRAY(
                    SELECT value
                    FROM unnest(display_tags || %s::text[])
                        WITH ORDINALITY AS item(value, position)
                    GROUP BY value
                    ORDER BY min(position)
                ),
                classification_version = greatest(classification_version, %s),
                salary_interval = coalesce(salary_interval, %s),
                salary_min = coalesce(salary_min, %s),
                salary_max = coalesce(salary_max, %s),
                salary_currency = coalesce(salary_currency, %s),
                salary_source = coalesce(salary_source, %s),
                salary_annual_min = coalesce(salary_annual_min, %s),
                salary_annual_max = coalesce(salary_annual_max, %s),
                source_skills = ARRAY(
                    SELECT DISTINCT value
                    FROM unnest(source_skills || %s::text[]) AS value
                    ORDER BY value
                ),
                last_seen_at = GREATEST(last_seen_at, %s),
                last_verified_at = GREATEST(last_verified_at, %s),
                status = 1,
                closed_at = NULL,
                updated_at = now()
            WHERE id = %s
            """,
            (
                incoming.description,
                incoming.description,
                incoming.description,
                hex_bytes(incoming.dedupe.description_hash),
                incoming.location.city,
                incoming.location.region_code,
                incoming.location.region_name,
                incoming.location.region_type,
                incoming.location.country_code,
                incoming.location.country_name,
                incoming.location.normalized,
                incoming.work_mode.value,
                incoming.employment_types,
                incoming.primary_employment_type,
                incoming.opportunity_type.value,
                incoming.opportunity_type.value,
                [item.value for item in incoming.schedule_types],
                incoming.primary_schedule_type.value,
                incoming.primary_schedule_type.value,
                incoming.job_category.value,
                incoming.job_category.value,
                incoming.job_subcategories,
                incoming.skill_tags,
                incoming.requirement_tags,
                incoming.display_tags,
                incoming.classification_version,
                incoming.salary.interval if incoming.salary else None,
                incoming.salary.minimum if incoming.salary else None,
                incoming.salary.maximum if incoming.salary else None,
                incoming.salary.currency if incoming.salary else None,
                incoming.salary.source if incoming.salary else None,
                incoming.salary.annualized_minimum if incoming.salary else None,
                incoming.salary.annualized_maximum if incoming.salary else None,
                incoming.source_skills,
                scraped_at,
                scraped_at,
                job_id,
            ),
        )

    async def _insert_snapshot(
        self,
        connection: AsyncConnection,
        *,
        run_id: int,
        source_id: int,
        fingerprint: bytes,
        payload_hash: bytes,
        payload: dict[str, Any],
        scraped_at: datetime,
    ) -> None:
        await connection.execute(
            """
            INSERT INTO raw_job_snapshots (
                scraped_at, ingestion_run_id, job_source_id,
                source_fingerprint, payload_hash, payload
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (scraped_at, run_id, source_id, fingerprint, payload_hash, Jsonb(payload)),
        )


CANDIDATE_SELECT = """
    SELECT j.id,
           j.title_normalized,
           j.company_normalized,
           j.location_normalized,
           j.work_mode,
           j.date_posted,
           left(j.description, 12000) AS description,
           encode(j.description_hash, 'hex') AS description_hash,
           array_remove(array_agg(encode(all_sources.direct_url_hash, 'hex')), NULL)
               AS direct_url_hashes
    FROM jobs j
    LEFT JOIN job_sources all_sources ON all_sources.job_id = j.id
"""


def candidate_from_row(row: dict[str, Any]) -> CandidateJob:
    return CandidateJob(
        job_id=row["id"],
        title_normalized=row["title_normalized"],
        company_normalized=row["company_normalized"],
        location_normalized=row["location_normalized"],
        work_mode=row["work_mode"],
        date_posted=row["date_posted"],
        description=row["description"],
        description_hash=row["description_hash"],
        direct_url_hashes=frozenset(row["direct_url_hashes"] or []),
    )


def stable_json_hash(payload: dict[str, Any]) -> bytes:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(serialized).digest()


def advisory_key(value: bytes) -> int:
    return int.from_bytes(value[:8], byteorder="big", signed=True)


def hex_bytes(value: str | None) -> bytes | None:
    return bytes.fromhex(value) if value else None
