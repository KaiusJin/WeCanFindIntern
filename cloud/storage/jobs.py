"""Transactional, idempotent job ingestion with indexed dedupe candidate lookup."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from psycopg import AsyncConnection, sql
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from cloud.storage.deduplication import (
    CandidateJob,
    DedupeAction,
    choose_duplicate,
)
from wecanfindintern.domain.classification import classify_job
from wecanfindintern.domain.jobs import (
    CanonicalJobInput,
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

        batch_jobs = list(jobs)
        counts: Counter[str] = Counter()
        if not batch_jobs:
            return counts
        async with self.pool.connection() as connection, connection.transaction():
            await connection.execute(
                "SELECT ensure_raw_job_snapshot_partition(%s)",
                (scraped_at,),
            )
            # Lock the whole batch in one globally ordered request, before reads.
            lock_keys = sorted(
                {
                    advisory_key(bytes.fromhex(value))
                    for job in batch_jobs
                    for value in (
                        job.source.source_fingerprint,
                        job.dedupe.block_key,
                        job.dedupe.direct_url_hash,
                    )
                    if value
                }
            )
            with self.timings.measure("locks"):
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(key) "
                    "FROM (SELECT unnest(%s::bigint[]) AS key ORDER BY key) locks",
                    (lock_keys,),
                )
            # Repeated source identities must be processed in order, including a
            # changed payload following an unchanged one within the same batch.
            occurrences = Counter(job.source.source_fingerprint for job in batch_jobs)
            with self.timings.measure("bulk_refresh"):
                unchanged_fingerprints = await self._refresh_unchanged_sources(
                    connection,
                    jobs=[
                        job for job in batch_jobs if occurrences[job.source.source_fingerprint] == 1
                    ],
                    scraped_at=scraped_at,
                )
            counts[IngestionOutcome.UNCHANGED.value] += len(unchanged_fingerprints)
            pending = [
                job
                for job in batch_jobs
                if job.source.source_fingerprint not in unchanged_fingerprints
            ]
            if pending:
                counts.update(
                    await self._ingest_changed_batch(
                        connection, jobs=pending, run_id=run_id, scraped_at=scraped_at
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
                    counts[IngestionOutcome.CREATED.value],
                    counts[IngestionOutcome.MERGED.value],
                    counts[IngestionOutcome.UPDATED.value],
                    counts[IngestionOutcome.UNCHANGED.value],
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
                        last_scraped_at = %s,
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

    async def _ingest_changed_batch(
        self,
        connection: AsyncConnection,
        *,
        jobs: list[CanonicalJobInput],
        run_id: int,
        scraped_at: datetime,
    ) -> Counter[str]:
        with self.timings.measure("source_lookup"):
            sources = await (
                await connection.execute(
                    "SELECT * FROM job_sources WHERE source_fingerprint = ANY(%s::bytea[]) "
                    "ORDER BY id FOR UPDATE",
                    ([bytes.fromhex(job.source.source_fingerprint) for job in jobs],),
                )
            ).fetchall()
        sources = {bytes(row["source_fingerprint"]).hex(): row for row in sources}
        with self.timings.measure("candidates"):
            # Fetch only the batch's indexed blocks/direct URLs and known sources.
            # Load complete blocks so changes earlier in the batch are visible to
            # later decisions; scoring still uses 10 direct + 25 blocked candidates.
            records = await (
                await connection.execute(
                    """
                SELECT j.*, ARRAY(
                    SELECT encode(s.direct_url_hash, 'hex') FROM job_sources s
                    WHERE s.job_id = j.id AND s.direct_url_hash IS NOT NULL
                ) AS direct_url_hashes
                FROM jobs j
                WHERE j.id = ANY(%s::bigint[]) OR (
                    j.status = 1 AND (
                        j.dedupe_block_key = ANY(%s::bytea[]) OR j.id IN (
                            SELECT job_id FROM job_sources
                            WHERE direct_url_hash = ANY(%s::bytea[])
                        )
                    )
                )
                ORDER BY j.id FOR UPDATE OF j
                """,
                    (
                        list({row["job_id"] for row in sources.values()}),
                        list({bytes.fromhex(job.dedupe.block_key) for job in jobs}),
                        list(
                            {
                                bytes.fromhex(job.dedupe.direct_url_hash)
                                for job in jobs
                                if job.dedupe.direct_url_hash
                            }
                        ),
                    ),
                )
            ).fetchall()
        records = {row["id"]: row for row in records}
        for row in records.values():
            row["direct_url_hashes"] = Counter(row["direct_url_hashes"])
        changed_jobs: set[int] = set()
        changed_sources: set[str] = set()
        snapshots = []
        decisions = []
        counts: Counter[str] = Counter()
        for incoming in jobs:
            fingerprint = incoming.source.source_fingerprint
            payload_hash = stable_json_hash(incoming.source.payload)
            source = sources.get(fingerprint)
            persisted = normalize_canonical_job_description(incoming)
            if source is not None:
                job_id = source["job_id"]
                current = records[job_id]
                changed = payload_hash != source["last_payload_hash"]
                if changed:
                    replacement = job_values(persisted, scraped_at)
                    current.update({name: replacement[name] for name in SOURCE_OWNED_JOB_FIELDS})
                refresh_canonical_fields(current, persisted, scraped_at)
                old_hash = source["direct_url_hash"]
                if old_hash:
                    current["direct_url_hashes"][bytes(old_hash).hex()] -= 1
                outcome = IngestionOutcome.UPDATED if changed else IngestionOutcome.UNCHANGED
            else:
                with self.timings.measure("candidates"):
                    decision = choose_duplicate(incoming, batch_candidates(incoming, records))
                if decision.action is DedupeAction.MERGE:
                    job_id = decision.candidate_job_id
                    current = records[job_id]
                    refresh_canonical_fields(current, persisted, scraped_at)
                    outcome = IngestionOutcome.MERGED
                else:
                    # Temporary IDs let later records dedupe against this new job
                    # before a single bulk INSERT assigns the real database IDs.
                    job_id = -(len(changed_jobs) + 1)
                    current = records[job_id] = {
                        **job_values(persisted, scraped_at),
                        "id": job_id,
                        "public_id": uuid4(),
                        "direct_url_hashes": Counter(),
                    }
                    outcome = IngestionOutcome.CREATED
                current["source_count"] += 1
                source = sources[fingerprint] = {
                    "id": None,
                    "job_id": job_id,
                    "source": incoming.source.source,
                    "source_job_id": incoming.source.source_job_id,
                    "source_fingerprint": bytes.fromhex(fingerprint),
                    "first_seen_at": incoming.first_seen_at,
                    "last_seen_at": scraped_at,
                    "details_fetched_at": None,
                }
                changed = True
                decisions.append(
                    {
                        "source_fingerprint": bytes.fromhex(fingerprint),
                        "resulting_job_id": job_id,
                        "compared_job_id": decision.candidate_job_id,
                        "action": decision.action.value,
                        "score": decision.score,
                        "algorithm_version": 2,
                        "evidence": decision.evidence,
                    }
                )
            if incoming.dedupe.direct_url_hash:
                current["direct_url_hashes"][incoming.dedupe.direct_url_hash] += 1
            source.update(
                {
                    "source_url": incoming.source.source_url,
                    "canonical_source_url": incoming.source.canonical_source_url,
                    "direct_url": incoming.source.direct_url,
                    "canonical_direct_url": incoming.source.canonical_direct_url,
                    "direct_url_hash": hex_bytes(incoming.dedupe.direct_url_hash),
                    "last_payload_hash": payload_hash,
                    "details_fetched_at": latest(
                        source["details_fetched_at"], incoming.source.details_fetched_at
                    ),
                    "last_seen_at": latest(source["last_seen_at"], scraped_at),
                    "last_scraped_at": scraped_at,
                }
            )
            if changed:
                snapshots.append(
                    {
                        "scraped_at": scraped_at,
                        "ingestion_run_id": run_id,
                        "job_source_id": fingerprint,  # resolved after bulk source INSERT
                        "source_fingerprint": bytes.fromhex(fingerprint),
                        "payload_hash": payload_hash,
                        "payload": persisted.source.payload,
                    }
                )
            changed_jobs.add(job_id)
            changed_sources.add(fingerprint)
            counts[outcome.value] += 1

        with self.timings.measure("writes"):
            new_jobs = [records[key] for key in sorted(changed_jobs, reverse=True) if key < 0]
            inserted = await insert_rows(
                connection,
                "jobs",
                [{"public_id": row["public_id"], **project(row, JOB_FIELDS)} for row in new_jobs],
                returning=("id", "public_id"),
            )
            by_public_id = {row["public_id"]: row["id"] for row in inserted}
            job_ids = {row["id"]: by_public_id[row["public_id"]] for row in new_jobs}
            await update_rows(
                connection,
                "jobs",
                [
                    {"id": key, **project(records[key], JOB_FIELDS)}
                    for key in sorted(changed_jobs)
                    if key > 0
                ],
            )
            pending_sources = [sources[key] for key in sorted(changed_sources)]
            for row in pending_sources:
                row["job_id"] = job_ids.get(row["job_id"], row["job_id"])
            inserted_sources = await insert_rows(
                connection,
                "job_sources",
                [project(row, SOURCE_FIELDS) for row in pending_sources if row["id"] is None],
                returning=("id", "source_fingerprint"),
            )
            await update_rows(
                connection,
                "job_sources",
                [
                    {"id": row["id"], **project(row, SOURCE_FIELDS)}
                    for row in pending_sources
                    if row["id"] is not None
                ],
            )
            for row in inserted_sources:
                sources[bytes(row["source_fingerprint"]).hex()]["id"] = row["id"]
            for snapshot in snapshots:
                snapshot["job_source_id"] = sources[snapshot["job_source_id"]]["id"]
            for decision in decisions:
                for field in ("resulting_job_id", "compared_job_id"):
                    decision[field] = job_ids.get(decision[field], decision[field])
            await insert_rows(connection, "raw_job_snapshots", snapshots)
            await insert_rows(connection, "dedupe_decisions", decisions)
            await insert_rows(
                connection,
                "job_status_history",
                [
                    {"job_id": job_ids[row["id"]], "to_status": 1, "reason": "first_seen"}
                    for row in new_jobs
                ],
            )
        return counts


SOURCE_OWNED_JOB_FIELDS = [
    "title",
    "title_normalized",
    "company_name",
    "company_normalized",
    "company_industry",
    "company_website_url",
    "company_logo_url",
    "location_text",
    "city",
    "region_code",
    "region_name",
    "region_type",
    "country_code",
    "country_name",
    "location_normalized",
    "date_posted",
    "published_sort_at",
    "job_function",
    "contact_emails",
    "vacancy_count",
    "description",
    "description_hash",
    "work_mode",
    "employment_types",
    "primary_employment_type",
    "source_skills",
    "salary_interval",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_source",
    "salary_annual_min",
    "salary_annual_max",
]
JOB_FIELDS = SOURCE_OWNED_JOB_FIELDS + [
    "opportunity_type",
    "schedule_types",
    "primary_schedule_type",
    "job_category",
    "job_subcategories",
    "skill_tags",
    "requirement_tags",
    "display_tags",
    "classification_version",
    "dedupe_block_key",
    "first_seen_at",
    "last_seen_at",
    "last_verified_at",
    "status",
    "closed_at",
    "source_count",
]
SOURCE_FIELDS = [
    "job_id",
    "source",
    "source_job_id",
    "source_url",
    "canonical_source_url",
    "direct_url",
    "canonical_direct_url",
    "direct_url_hash",
    "source_fingerprint",
    "last_payload_hash",
    "details_fetched_at",
    "first_seen_at",
    "last_seen_at",
    "last_scraped_at",
]


def project(row: dict[str, Any], columns: list[str]) -> dict[str, Any]:
    return {name: row[name] for name in columns}


def latest(left, right):
    return max(value for value in (left, right) if value is not None) if left or right else None


def job_values(incoming: CanonicalJobInput, scraped_at: datetime) -> dict[str, Any]:
    salary = incoming.salary
    return {
        "title": incoming.title,
        "title_normalized": incoming.title_normalized,
        "company_name": incoming.company.name,
        "company_normalized": incoming.company.normalized_name,
        "company_industry": incoming.company.industry,
        "company_website_url": incoming.company.website_url,
        "company_logo_url": incoming.company.logo_url,
        "location_text": incoming.location.raw,
        "city": incoming.location.city,
        "region_code": incoming.location.region_code,
        "region_name": incoming.location.region_name,
        "region_type": incoming.location.region_type,
        "country_code": incoming.location.country_code,
        "country_name": incoming.location.country_name,
        "location_normalized": incoming.location.normalized,
        "work_mode": incoming.work_mode.value,
        "employment_types": incoming.employment_types,
        "primary_employment_type": incoming.primary_employment_type,
        "opportunity_type": incoming.opportunity_type.value,
        "schedule_types": [item.value for item in incoming.schedule_types],
        "primary_schedule_type": incoming.primary_schedule_type.value,
        "job_category": incoming.job_category.value,
        "job_subcategories": incoming.job_subcategories,
        "skill_tags": incoming.skill_tags,
        "requirement_tags": incoming.requirement_tags,
        "display_tags": incoming.display_tags,
        "classification_version": incoming.classification_version,
        "date_posted": incoming.date_posted,
        "published_sort_at": incoming.published_sort_at,
        "job_function": incoming.job_function,
        "description": incoming.description,
        "description_hash": hex_bytes(incoming.dedupe.description_hash),
        "salary_interval": salary.interval if salary else None,
        "salary_min": salary.minimum if salary else None,
        "salary_max": salary.maximum if salary else None,
        "salary_currency": salary.currency if salary else None,
        "salary_source": salary.source if salary else None,
        "salary_annual_min": salary.annualized_minimum if salary else None,
        "salary_annual_max": salary.annualized_maximum if salary else None,
        "source_skills": incoming.source_skills,
        "contact_emails": incoming.contact_emails,
        "vacancy_count": incoming.vacancy_count,
        "dedupe_block_key": bytes.fromhex(incoming.dedupe.block_key),
        "first_seen_at": incoming.first_seen_at,
        "last_seen_at": scraped_at,
        "last_verified_at": scraped_at,
        "status": 1,
        "closed_at": None,
        "source_count": 0,
    }


def refresh_canonical_fields(
    current: dict[str, Any], incoming: CanonicalJobInput, scraped_at: datetime
) -> None:
    """Apply the same merge rules in memory before writing the batch once."""
    values = job_values(incoming, scraped_at)
    if (len(incoming.description or ""), incoming.description or "") > (
        len(current["description"] or ""),
        current["description"] or "",
    ):
        current["description"] = incoming.description
        current["description_hash"] = values["description_hash"]
    for key in (
        "city",
        "region_code",
        "region_name",
        "region_type",
        "country_code",
        "country_name",
        "primary_employment_type",
        "salary_interval",
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_source",
        "salary_annual_min",
        "salary_annual_max",
    ):
        if current[key] is None:
            current[key] = values[key]
    if current["location_normalized"] == "":
        current["location_normalized"] = values["location_normalized"]
    if current["work_mode"] == "unknown":
        current["work_mode"] = values["work_mode"]
    for key in ("employment_types", "source_skills"):
        current[key] = sorted(set(current[key] or []) | set(values[key]))
    classification = classify_job(
        title=current["title"],
        description=current["description"],
        employment_types=current["employment_types"],
        source_skills=current["source_skills"],
        work_mode=current["work_mode"],
    )
    current.update(
        {
            "opportunity_type": classification.opportunity_type.value,
            "schedule_types": [item.value for item in classification.schedule_types],
            "primary_schedule_type": classification.primary_schedule_type.value,
            "job_category": classification.job_category.value,
            "job_subcategories": classification.job_subcategories,
            "skill_tags": classification.skill_tags,
            "requirement_tags": classification.requirement_tags,
            "display_tags": classification.display_tags,
            "classification_version": classification.classification_version,
            "last_seen_at": latest(current["last_seen_at"], scraped_at),
            "last_verified_at": latest(current["last_verified_at"], scraped_at),
            "status": 1,
            "closed_at": None,
        }
    )


def batch_candidates(incoming: CanonicalJobInput, records: dict[int, dict]) -> list[CandidateJob]:
    active = [row for row in records.values() if row["status"] == 1]
    direct = [
        row for row in active if row["direct_url_hashes"][incoming.dedupe.direct_url_hash] > 0
    ][:10]
    blocked = [
        row
        for row in active
        if bytes(row["dedupe_block_key"]).hex() == incoming.dedupe.block_key
        and (
            incoming.date_posted is None
            or row["date_posted"] is None
            or abs((row["date_posted"] - incoming.date_posted).days) <= 60
        )
    ]
    # Negative temporary IDs sort as newly inserted IDs, newest first.
    blocked.sort(
        key=lambda row: (
            abs((row["date_posted"] - incoming.date_posted).days)
            if row["date_posted"] and incoming.date_posted
            else 0,
            (0, row["id"]) if row["id"] < 0 else (1, -row["id"]),
        )
    )
    selected = {row["id"]: row for row in [*direct, *blocked[:25]]}
    return [
        CandidateJob(
            job_id=row["id"],
            title_normalized=row["title_normalized"],
            company_normalized=row["company_normalized"],
            location_normalized=row["location_normalized"],
            work_mode=row["work_mode"],
            date_posted=row["date_posted"],
            description=row["description"][:12000] if row["description"] else row["description"],
            description_hash=bytes(row["description_hash"]).hex()
            if row["description_hash"]
            else None,
            direct_url_hashes=frozenset(
                key for key, count in row["direct_url_hashes"].items() if count > 0
            ),
        )
        for row in selected.values()
    ]


def json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (bytes, memoryview)):
        return "\\x" + bytes(value).hex()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def row_payload(rows: list[dict]) -> Jsonb:
    return Jsonb(rows, dumps=lambda value: json.dumps(value, default=json_default))


async def insert_rows(
    connection: AsyncConnection, table: str, rows: list[dict], *, returning: tuple[str, ...] = ()
) -> list[dict]:
    if not rows:
        return []
    columns = sql.SQL(", ").join(map(sql.Identifier, rows[0]))
    query = sql.SQL(
        "INSERT INTO {table} ({columns}) "
        "SELECT {columns} FROM jsonb_populate_recordset(NULL::{table}, %s)"
    ).format(table=sql.Identifier(table), columns=columns)
    if returning:
        query += sql.SQL(" RETURNING ") + sql.SQL(", ").join(map(sql.Identifier, returning))
    result = await connection.execute(query, (row_payload(rows),))
    return await result.fetchall() if returning else []


async def update_rows(connection: AsyncConnection, table: str, rows: list[dict]) -> None:
    if not rows:
        return
    assignments = sql.SQL(", ").join(
        sql.SQL("{name} = incoming.{name}").format(name=sql.Identifier(name))
        for name in rows[0]
        if name != "id"
    )
    await connection.execute(
        sql.SQL(
            "UPDATE {table} target SET {assignments}, updated_at = now() "
            "FROM jsonb_populate_recordset(NULL::{table}, %s) incoming "
            "WHERE target.id = incoming.id"
        ).format(table=sql.Identifier(table), assignments=assignments),
        (row_payload(rows),),
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
