"""Transactional, idempotent job ingestion with indexed dedupe candidate lookup."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from time import perf_counter
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from wecanfindintern.deduplication import (
    CandidateJob,
    DedupeAction,
    DedupeDecision,
    choose_duplicate,
    group_round_jobs,
    record_completeness,
)
from wecanfindintern.domain.classification import classify_job
from wecanfindintern.domain.jobs import (
    CanonicalJobInput,
    SalaryRange,
    normalize_canonical_job_description,
)


class IngestionOutcome(StrEnum):
    CREATED = "created"
    MERGED = "merged"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class IngestionRun:
    internal_id: int
    public_id: UUID


class DedupeTimings:
    """Client-side wall time for nonoverlapping phases of one ingestion run."""

    def __init__(self) -> None:
        self.seconds = dict.fromkeys(
            ("normalize", "locks", "source_lookup", "candidates", "writes", "bulk_refresh"), 0.0
        )

    @contextmanager
    def measure(self, phase: str) -> Iterator[None]:
        started = perf_counter()
        try:
            yield
        finally:
            self.seconds[phase] += perf_counter() - started

    def summary(self, elapsed: float) -> dict[str, float]:
        other = max(0.0, elapsed - sum(self.seconds.values()))
        return {
            k: round(v, 3) for k, v in {**self.seconds, "other": other, "total": elapsed}.items()
        }


class JobIngestionRepository:
    """Write path optimized for bounded batches, not one connection per row."""

    def __init__(self, pool: AsyncConnectionPool, timings: DedupeTimings | None = None) -> None:
        self.pool = pool
        self.timings = timings if timings is not None else DedupeTimings()

    async def start_run(
        self,
        *,
        sources: list[str],
        query: dict[str, Any],
        provider: str = "jobspy",
    ) -> IngestionRun:
        async with self.pool.connection() as connection:
            row = await (
                await connection.execute(
                    """
                    INSERT INTO ingestion_runs (
                        provider, sources, query
                    )
                    VALUES (%s, %s, %s)
                    RETURNING id, public_id
                    """,
                    (provider, sources, Jsonb(query)),
                )
            ).fetchone()
        return IngestionRun(internal_id=row["id"], public_id=row["public_id"])

    async def history_limit(self) -> int:
        """Freeze the historical candidate boundary before writing the first round group."""
        async with self.pool.connection() as connection:
            row = await (
                await connection.execute("SELECT coalesce(max(id), 0) AS id FROM jobs")
            ).fetchone()
        return row["id"]

    async def ingest_batch(
        self, *, run_id: int, jobs: Iterable[CanonicalJobInput], scraped_at: datetime
    ) -> Counter[str]:
        groups = group_round_jobs(jobs)
        return await self.ingest_groups(
            run_id=run_id,
            groups=groups,
            history_limit=await self.history_limit(),
            observed_at=scraped_at,
        )

    async def ingest_groups(
        self,
        *,
        run_id: int,
        groups: list[list[CanonicalJobInput]],
        history_limit: int,
        observed_at: datetime | None = None,
    ) -> Counter[str]:
        """Store complete round groups and all their source links in one transaction."""
        counts: Counter[str] = Counter()
        records = [job for group in groups for job in group]
        if not records:
            return counts
        by_time: dict[datetime, list[CanonicalJobInput]] = {}
        lock_keys = set()
        for job in records:
            by_time.setdefault(observed_at or job.first_seen_at, []).append(job)
            for key in (
                job.source.source_fingerprint,
                job.dedupe.block_key,
                job.dedupe.direct_url_hash,
            ):
                if key:
                    lock_keys.add(advisory_key(bytes.fromhex(key)))

        async with self.pool.connection() as connection, connection.transaction():
            with self.timings.measure("locks"):
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(k) FROM "
                    "(SELECT k FROM unnest(%s::bigint[]) k ORDER BY k) keys",
                    (sorted(lock_keys),),
                )
            unchanged = set()
            with self.timings.measure("bulk_refresh"):
                for when, sightings in sorted(by_time.items()):
                    await connection.execute(
                        "SELECT ensure_raw_job_snapshot_partition(%s)", (when,)
                    )
                    unchanged.update(
                        await self._refresh_unchanged_sources(
                            connection, jobs=sightings, scraped_at=when
                        )
                    )
            with self.timings.measure("source_lookup"):
                rows = await (
                    await connection.execute(
                        """SELECT id, job_id, encode(source_fingerprint, 'hex') AS fingerprint,
                              last_payload_hash FROM job_sources
                       WHERE source_fingerprint = ANY(%s::bytea[]) ORDER BY id FOR UPDATE""",
                        ([bytes.fromhex(job.source.source_fingerprint) for job in records],),
                    )
                ).fetchall()
                existing = {row["fingerprint"]: row for row in rows}
                current_quality = {}
                if existing:
                    historic = await (
                        await connection.execute(
                            "SELECT " + CANONICAL_FIELDS + " FROM jobs j WHERE j.id=ANY(%s) "
                            "ORDER BY j.id FOR UPDATE",
                            (sorted({row["job_id"] for row in rows}),),
                        )
                    ).fetchall()
                    current_quality = {row["id"]: record_completeness(row) for row in historic}
            for group in groups:
                counts.update(
                    await self._store_group(
                        connection,
                        run_id,
                        group,
                        existing,
                        unchanged,
                        current_quality,
                        history_limit,
                        observed_at,
                    )
                )
            await connection.execute(
                """
                UPDATE ingestion_runs
                SET fetched_count = fetched_count + %s,
                    created_count = created_count + %s,
                    merged_count = merged_count + %s,
                    updated_count = updated_count + %s,
                    unchanged_count = unchanged_count + %s
                WHERE id = %s
                """,
                (
                    sum(counts.values()),
                    counts["created"],
                    counts["merged"],
                    counts["updated"],
                    counts["unchanged"],
                    run_id,
                ),
            )
        return counts

    async def _refresh_unchanged_sources(
        self,
        connection: AsyncConnection,
        *,
        jobs: list[CanonicalJobInput],
        scraped_at: datetime,
    ) -> set[str]:
        """Refresh identical source rows in one statement and bypass full dedupe."""

        if not jobs:
            return set()
        fingerprints = [bytes.fromhex(job.source.source_fingerprint) for job in jobs]
        payload_hashes = [stable_json_hash(job.source.payload) for job in jobs]
        detail_times = [job.source.details_fetched_at for job in jobs]
        rows = await (
            await connection.execute(
                """
                WITH incoming(source_fingerprint, payload_hash, details_fetched_at) AS (
                    SELECT * FROM unnest(%s::bytea[], %s::bytea[], %s::timestamptz[])
                ),
                touched_sources AS (
                    UPDATE job_sources js
                    SET last_seen_at = GREATEST(js.last_seen_at, %s),
                        last_scraped_at = GREATEST(js.last_scraped_at, %s),
                        details_fetched_at = CASE
                            WHEN incoming.details_fetched_at IS NULL
                                THEN js.details_fetched_at
                            ELSE GREATEST(
                                coalesce(js.details_fetched_at, incoming.details_fetched_at),
                                incoming.details_fetched_at
                            )
                        END,
                        updated_at = now()
                    FROM incoming
                    WHERE js.source_fingerprint = incoming.source_fingerprint
                      AND js.last_payload_hash = incoming.payload_hash
                    RETURNING js.job_id, js.source_fingerprint
                ),
                refreshed_jobs AS (
                    UPDATE jobs j
                    SET last_seen_at = GREATEST(j.last_seen_at, %s),
                        last_verified_at = GREATEST(j.last_verified_at, %s),
                        status = 1,
                        closed_at = NULL,
                        updated_at = now()
                    WHERE j.id IN (SELECT job_id FROM touched_sources)
                    RETURNING j.id
                )
                SELECT encode(source_fingerprint, 'hex') AS source_fingerprint
                FROM touched_sources
                """,
                (
                    fingerprints,
                    payload_hashes,
                    detail_times,
                    scraped_at,
                    scraped_at,
                    scraped_at,
                    scraped_at,
                ),
            )
        ).fetchall()
        return {row["source_fingerprint"] for row in rows}

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

    async def _store_group(
        self,
        connection,
        run_id,
        group,
        existing,
        unchanged,
        current_quality,
        history_limit,
        observed_at,
    ) -> Counter[str]:
        counts: Counter[str] = Counter()
        keys = [job.source.source_fingerprint for job in group]
        anchors = {existing[key]["job_id"] for key in keys if key in existing}
        primary = group[0]
        if primary.salary is None:
            salary = next((job.salary for job in group if job.salary is not None), None)
            if salary is not None:
                primary = primary.model_copy(update={"salary": salary})
        if len(anchors) == 1 and all(key in unchanged for key in keys):
            old_quality = current_quality.get(next(iter(anchors)))
            if old_quality is not None and _canonical_completeness(primary) <= old_quality:
                return Counter(unchanged=len(group))
        first_seen = observed_at or min(job.first_seen_at for job in group)
        last_seen = observed_at or max(job.first_seen_at for job in group)
        with self.timings.measure("candidates"):
            decisions = []
            for job in group:
                if job.source.source_fingerprint in existing and (
                    job.source.source != "linkedin" or job.source.source_job_id
                ):
                    continue
                candidates = await self._find_candidates(connection, job, history_limit)
                match = choose_duplicate(job, candidates)
                decisions.append(match)
                if match.action is DedupeAction.MERGE:
                    anchors.add(match.candidate_job_id)
            if anchors:
                target = min(anchors)
                if len(anchors) > 1:
                    rows = await (
                        await connection.execute(
                            CANONICAL_RECORD_SELECT
                            + " WHERE j.id = ANY(%s) ORDER BY j.id FOR UPDATE",
                            (sorted(anchors),),
                        )
                    ).fetchall()
                    target = max(rows, key=record_completeness)["id"]
                    first_seen = min(first_seen, *(row["first_seen_at"] for row in rows))
                    last_seen = max(last_seen, *(row["last_seen_at"] for row in rows))
                decision = next(
                    (
                        match
                        for match in decisions
                        if match.action is DedupeAction.MERGE and match.candidate_job_id == target
                    ),
                    DedupeDecision(DedupeAction.MERGE, target, 1.0, {"source_identity": True}),
                )
            else:
                decision = max(decisions, key=lambda item: item.score)
                target = None

        created = target is None
        with self.timings.measure("writes"):
            primary = normalize_canonical_job_description(primary)
            if created:
                primary = primary.model_copy(update={"first_seen_at": first_seen})
                target = await self._insert_job(connection, primary, last_seen)
            else:
                await self._refresh_canonical_job(
                    connection,
                    target,
                    primary,
                    last_seen,
                    refresh_equal=(
                        primary.source.source_fingerprint in existing
                        and primary.source.source_fingerprint not in unchanged
                    ),
                )
            current_quality.pop(target, None)

            moved_from = anchors - {target}
            for index, job in enumerate(group):
                key = job.source.source_fingerprint
                fingerprint = bytes.fromhex(key)
                when = observed_at or job.first_seen_at
                old = existing.get(key)
                if old and old["job_id"] == target and key in unchanged:
                    counts["unchanged"] += 1
                    continue
                payload_hash = stable_json_hash(job.source.payload)
                if old:
                    moved = old["job_id"] != target
                    changed = await self._refresh_existing_source(
                        connection,
                        incoming=job,
                        source_id=old["id"],
                        payload_hash=payload_hash,
                        previous_payload_hash=old["last_payload_hash"],
                        run_id=run_id,
                        scraped_at=when,
                    )
                    if not moved:
                        counts["updated" if changed else "unchanged"] += 1
                        continue
                else:
                    source_id = await self._insert_source(
                        connection,
                        job_id=target,
                        incoming=job,
                        fingerprint=fingerprint,
                        payload_hash=payload_hash,
                        scraped_at=when,
                    )
                    await self._insert_snapshot(
                        connection,
                        run_id=run_id,
                        source_id=source_id,
                        fingerprint=fingerprint,
                        payload_hash=payload_hash,
                        payload=job.source.payload,
                        scraped_at=when,
                    )
                is_new = created and index == 0
                counts["created" if is_new else "merged"] += 1
                await connection.execute(
                    """INSERT INTO dedupe_decisions (
                           source_fingerprint, resulting_job_id, compared_job_id,
                           action, score, algorithm_version, evidence
                       ) VALUES (%s, %s, %s, %s, %s, 3, %s)""",
                    (
                        fingerprint,
                        target,
                        decision.candidate_job_id if index == 0 else target,
                        "create" if is_new else "merge",
                        decision.score if index == 0 else 1.0,
                        Jsonb({**decision.evidence, "round_sources": len(group)}),
                    ),
                )
            if moved_from:
                # Move the job's other known source links too, including sources absent this round.
                moved_rows = await (
                    await connection.execute(
                        """UPDATE job_sources SET job_id=%s, updated_at=now()
                       WHERE job_id=ANY(%s) RETURNING encode(source_fingerprint, 'hex') AS fp""",
                        (target, sorted(moved_from)),
                    )
                ).fetchall()
                for row in moved_rows:
                    if row["fp"] in existing:
                        existing[row["fp"]]["job_id"] = target
                await connection.execute(
                    "UPDATE jobs SET first_seen_at=LEAST(first_seen_at,%s) WHERE id=%s",
                    (first_seen, target),
                )
            if counts["created"] or counts["merged"]:
                await connection.execute(
                    """WITH totals AS (
                           SELECT j.id, count(s.id) AS n FROM jobs j
                           LEFT JOIN job_sources s ON s.job_id=j.id
                           WHERE j.id=ANY(%s) GROUP BY j.id
                       )
                       UPDATE jobs j SET source_count=totals.n,
                           status=CASE WHEN totals.n=0 THEN 4 ELSE j.status END,
                           closed_at=CASE WHEN totals.n=0 THEN now() ELSE j.closed_at END,
                           updated_at=now()
                       FROM totals WHERE totals.id=j.id""",
                    (sorted(moved_from | {target}),),
                )
        return counts

    async def _find_candidates(
        self,
        connection: AsyncConnection,
        incoming: CanonicalJobInput,
        history_limit: int,
    ) -> list[CandidateJob]:
        rows_by_id: dict[int, dict[str, Any]] = {}
        direct_hash = hex_bytes(incoming.dedupe.direct_url_hash)
        history = " AND j.id <= %s"
        params = [history_limit]
        if incoming.source.source == "linkedin" and incoming.source.source_job_id:
            # Filter before LIMIT so unrelated LinkedIn IDs cannot crowd out an Indeed match.
            history += """
                AND (NOT EXISTS (SELECT 1 FROM job_sources s WHERE s.job_id=j.id)
                     OR EXISTS (SELECT 1 FROM job_sources s WHERE s.job_id=j.id AND
                                (s.source <> 'linkedin' OR s.source_job_id IS NULL
                                 OR s.source_job_id = %s)))
            """
            params.append(incoming.source.source_job_id)
        if direct_hash:
            rows = await (
                await connection.execute(
                    CANDIDATE_SELECT
                    + """
                    JOIN job_sources match_source ON match_source.job_id = j.id
                    WHERE j.status = 1 AND match_source.direct_url_hash = %s
                    """
                    + history
                    + """
                    GROUP BY j.id
                    LIMIT 10
                    """,
                    (direct_hash, *params),
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
                """
                + history
                + """
                GROUP BY j.id
                ORDER BY abs(coalesce(j.date_posted, %s::date) - %s::date), j.id DESC
                LIMIT 25
                """,
                (
                    bytes.fromhex(incoming.dedupe.block_key),
                    incoming.date_posted,
                    incoming.date_posted,
                    incoming.date_posted,
                    *params,
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
                    source_fingerprint, last_payload_hash, details_fetched_at,
                    first_seen_at, last_seen_at, last_scraped_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
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
                    incoming.source.details_fetched_at,
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
        payload_hash: bytes,
        previous_payload_hash: bytes,
        run_id: int,
        scraped_at: datetime,
    ) -> bool:
        changed = payload_hash != previous_payload_hash
        if changed:
            previous = await (
                await connection.execute(
                    """SELECT payload, scraped_at,
                          (SELECT payload_hash FROM raw_job_snapshots WHERE job_source_id=%s
                           ORDER BY scraped_at DESC, id DESC LIMIT 1) AS latest_hash
                   FROM raw_job_snapshots
                   WHERE job_source_id=%s AND payload_hash=%s
                   ORDER BY scraped_at DESC, id DESC LIMIT 1""",
                    (source_id, source_id, previous_payload_hash),
                )
            ).fetchone()
            if previous and (record_completeness(previous["payload"]), previous["scraped_at"]) > (
                record_completeness(incoming.source.payload),
                scraped_at,
            ):
                if previous["latest_hash"] != payload_hash:
                    await self._insert_snapshot(
                        connection,
                        run_id=run_id,
                        source_id=source_id,
                        fingerprint=bytes.fromhex(incoming.source.source_fingerprint),
                        payload_hash=payload_hash,
                        payload=incoming.source.payload,
                        scraped_at=scraped_at,
                    )
                await connection.execute(
                    """UPDATE job_sources SET last_seen_at=GREATEST(last_seen_at,%s),
                           last_scraped_at=GREATEST(last_scraped_at,%s),updated_at=now()
                       WHERE id=%s""",
                    (scraped_at, scraped_at, source_id),
                )
                return False
        await connection.execute(
            """
            UPDATE job_sources
            SET source_url = %s,
                canonical_source_url = %s,
                direct_url = %s,
                canonical_direct_url = %s,
                direct_url_hash = %s,
                last_payload_hash = %s,
                details_fetched_at = CASE
                    WHEN %s::timestamptz IS NULL THEN details_fetched_at
                    ELSE GREATEST(
                        coalesce(details_fetched_at, %s::timestamptz),
                        %s::timestamptz
                    )
                END,
                last_seen_at = GREATEST(last_seen_at, %s),
                last_scraped_at = GREATEST(last_scraped_at, %s),
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
                incoming.source.details_fetched_at,
                incoming.source.details_fetched_at,
                incoming.source.details_fetched_at,
                scraped_at,
                scraped_at,
                source_id,
            ),
        )
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
        return changed

    async def _replace_source_owned_fields(
        self,
        connection: AsyncConnection,
        job_id: int,
        incoming: CanonicalJobInput,
    ) -> None:
        """Replace primary posting fields when the incoming record wins the comparison."""

        await connection.execute(
            """UPDATE jobs SET
                title=%s,title_normalized=%s,
                company_name=%s,company_normalized=%s,company_industry=%s,
                company_website_url=%s,company_logo_url=%s,
                location_text=%s,city=%s,region_code=%s,region_name=%s,
                region_type=%s,country_code=%s,country_name=%s,
                location_normalized=%s,date_posted=%s,published_sort_at=%s,
                job_function=%s,contact_emails=%s::text[],vacancy_count=%s,
                primary_employment_type=coalesce(%s, primary_employment_type),dedupe_block_key=%s
            WHERE id=%s""",
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
                incoming.date_posted,
                incoming.published_sort_at,
                incoming.job_function,
                incoming.contact_emails,
                incoming.vacancy_count,
                incoming.primary_employment_type,
                bytes.fromhex(incoming.dedupe.block_key),
                job_id,
            ),
        )

    async def _refresh_canonical_job(
        self,
        connection: AsyncConnection,
        job_id: int,
        incoming: CanonicalJobInput,
        scraped_at: datetime,
        *,
        refresh_equal: bool = False,
    ) -> None:
        current = await (
            await connection.execute(
                CANONICAL_RECORD_SELECT + " WHERE j.id=%s FOR UPDATE",
                (job_id,),
            )
        ).fetchone()
        old_quality = record_completeness(current)
        new_quality = _canonical_completeness(incoming)
        prefer = new_quality > old_quality or (
            refresh_equal and new_quality == old_quality and scraped_at >= current["last_seen_at"]
        )
        salary = incoming.salary
        derived_salary = current["salary_source"] in {
            "description",
            "job_description",
            "linkedin_salary_label",
        }
        if current["interval"] and (salary is None or (not prefer and not derived_salary)):
            salary = SalaryRange(
                interval=current["interval"],
                minimum=current["min_amount"],
                maximum=current["max_amount"],
                currency=current["salary_currency"],
                source=current["salary_source"],
                annualized_minimum=current["salary_annual_min"],
                annualized_maximum=current["salary_annual_max"],
            )
        if prefer:
            await self._replace_source_owned_fields(connection, job_id, incoming)
        use_description = bool(incoming.description) and (prefer or not current["description"])
        canonical_description = incoming.description if use_description else current["description"]
        description_hash = (
            hex_bytes(incoming.dedupe.description_hash)
            if use_description
            else current["description_hash"]
        )
        employment_types = sorted(
            set(current["employment_types"] or []) | set(incoming.employment_types)
        )
        source_skills = sorted(set(current["source_skills"] or []) | set(incoming.source_skills))
        work_mode = current["work_mode"]
        if (
            (prefer and incoming.work_mode.value != "unknown")
            or not work_mode
            or work_mode == "unknown"
        ):
            work_mode = incoming.work_mode.value
        classification = classify_job(
            title=incoming.title if prefer else current["title"],
            description=canonical_description,
            employment_types=employment_types,
            source_skills=source_skills,
            work_mode=work_mode,
        )
        await connection.execute(
            """
            UPDATE jobs
            SET description = %s, description_hash = %s,
                city = coalesce(city, %s),
                region_code = coalesce(region_code, %s),
                region_name = coalesce(region_name, %s),
                region_type = coalesce(region_type, %s),
                country_code = coalesce(country_code, %s),
                country_name = coalesce(country_name, %s),
                location_normalized = CASE
                    WHEN location_normalized = '' THEN %s ELSE location_normalized
                END,
                work_mode = %s,
                employment_types = %s::text[],
                primary_employment_type = coalesce(primary_employment_type, %s),
                opportunity_type = %s,
                schedule_types = %s::text[],
                primary_schedule_type = %s,
                job_category = %s,
                job_subcategories = %s::text[],
                skill_tags = %s::text[],
                requirement_tags = %s::text[],
                display_tags = %s::text[],
                classification_version = %s,
                salary_interval = %s, salary_min = %s, salary_max = %s,
                salary_currency = %s, salary_source = %s,
                salary_annual_min = %s, salary_annual_max = %s,
                source_skills = %s::text[],
                last_seen_at = GREATEST(last_seen_at, %s),
                last_verified_at = GREATEST(last_verified_at, %s),
                status = 1,
                closed_at = NULL,
                updated_at = now()
            WHERE id = %s
            """,
            (
                canonical_description,
                description_hash,
                incoming.location.city,
                incoming.location.region_code,
                incoming.location.region_name,
                incoming.location.region_type,
                incoming.location.country_code,
                incoming.location.country_name,
                incoming.location.normalized,
                work_mode,
                employment_types,
                incoming.primary_employment_type,
                classification.opportunity_type.value,
                [item.value for item in classification.schedule_types],
                classification.primary_schedule_type.value,
                classification.job_category.value,
                classification.job_subcategories,
                classification.skill_tags,
                classification.requirement_tags,
                classification.display_tags,
                classification.classification_version,
                salary.interval if salary else None,
                salary.minimum if salary else None,
                salary.maximum if salary else None,
                salary.currency if salary else None,
                salary.source if salary else None,
                salary.annualized_minimum if salary else None,
                salary.annualized_maximum if salary else None,
                source_skills,
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


def _canonical_completeness(job: CanonicalJobInput) -> tuple[int, int, int]:
    fields = dict(job.source.payload, description=job.description)
    if job.salary:
        fields.update(
            min_amount=job.salary.minimum,
            max_amount=job.salary.maximum,
            interval=job.salary.interval,
        )
    return record_completeness(fields)


CANONICAL_FIELDS = """
           j.id, j.title, length(btrim(j.description)) AS description_length,
           j.description_hash, j.first_seen_at, j.last_seen_at,
           j.company_name AS company, j.location_text AS location, j.date_posted,
           j.employment_types, j.employment_types AS job_type, j.job_function, j.work_mode,
           j.company_industry, j.company_website_url AS company_url_direct,
           j.company_logo_url AS company_logo,
           j.source_skills, j.source_skills AS skills, j.contact_emails AS emails, j.vacancy_count,
           j.salary_interval AS interval, j.salary_min AS min_amount, j.salary_max AS max_amount,
           j.salary_currency, j.salary_source, j.salary_annual_min, j.salary_annual_max,
           EXISTS(SELECT 1 FROM job_sources s WHERE s.job_id=j.id AND s.direct_url IS NOT NULL)
               AS job_url_direct
"""
CANONICAL_RECORD_SELECT = "SELECT j.description, " + CANONICAL_FIELDS + " FROM jobs j"


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
               AS direct_url_hashes,
           jsonb_agg(DISTINCT jsonb_build_array(all_sources.source, all_sources.source_job_id))
               FILTER (WHERE all_sources.id IS NOT NULL) AS source_identities
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
        source_identities=frozenset(tuple(pair) for pair in row["source_identities"] or []),
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
