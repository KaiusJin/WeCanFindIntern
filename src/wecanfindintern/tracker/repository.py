"""PostgreSQL repository for the application tracker workspace."""

# ruff: noqa: SIM117

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from wecanfindintern.domain.salary import format_salary_text
from wecanfindintern.tracker.models import (
    APPLICATION_STAGE_LABELS,
    ApplicationStage,
    TrackedApplication,
    TrackedJobState,
    TrackerCreateRequest,
    TrackerEvent,
    TrackerOrigin,
    TrackerStatsResponse,
    TrackerUpdateRequest,
    application_stage_label,
)

APPLICATION_COLUMNS = """
    public_id AS id, job_id, external_job_id, company_name, title, location_text, work_mode,
    job_url, job_description, application_deadline, salary_text, origin_type,
    source_type AS source, stage, external_stage, external_status,
    applied_at, created_at, updated_at
"""

SORT_COLUMNS = {
    "updated": "updated_at",
    "created": "created_at",
    "applied": "applied_at",
    "company": "lower(company_name)",
}
BOOKMARK_CREATED_DETAILS = "Opportunity saved to pipeline"

def _stage_title_sql(expression: str) -> str:
    clauses = " ".join(
        f"WHEN '{stage.value}' THEN '{label}'"
        for stage, label in APPLICATION_STAGE_LABELS.items()
    )
    return f"CASE {expression} {clauses} ELSE initcap({expression}) END"


class TrackerRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    @staticmethod
    async def _record_bookmark_created_event(
        cursor: Any, application_public_id: UUID, occurred_at: datetime
    ) -> None:
        await cursor.execute(
            """
            INSERT INTO application_tracker_events (
                application_id, event_type, title, details, occurred_at
            )
            SELECT id, 'created', %s, %s, %s
            FROM application_tracker WHERE public_id = %s
            AND NOT EXISTS (
                SELECT 1 FROM application_tracker_events
                WHERE application_id = application_tracker.id AND event_type = 'created'
            );
            """,
            (
                application_stage_label(ApplicationStage.INTERESTED),
                BOOKMARK_CREATED_DETAILS,
                occurred_at,
                application_public_id,
            ),
        )

    async def _unbookmark(
        self, *, reference_predicate: str, parameters: tuple[Any, ...]
    ) -> tuple[bool, str | None]:
        """Delete an interested bookmark selected by one trusted repository predicate."""

        async with self.pool.connection() as connection, connection.transaction():
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    f"SELECT id, stage FROM application_tracker WHERE {reference_predicate};",
                    parameters,
                )
                row = await cursor.fetchone()
                if not row:
                    return False, None
                if row["stage"] != "interested":
                    return False, row["stage"]
                await cursor.execute(
                    "DELETE FROM application_tracker WHERE id = %s;",
                    (row["id"],),
                )
                return True, None

    @staticmethod
    def _list_where(
        *,
        query: str | None = None,
        stage: ApplicationStage | None = None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = ["archived_at IS NULL"]
        params: list[Any] = []
        if query:
            clauses.append("(company_name ILIKE %s OR title ILIKE %s OR location_text ILIKE %s)")
            term = f"%{query.strip()}%"
            params.extend([term] * 3)
        if stage:
            clauses.append("stage = %s")
            params.append(stage.value)
        return (" WHERE " + " AND ".join(clauses) if clauses else "", params)

    async def list_applications(
        self,
        *,
        query: str | None = None,
        stage: ApplicationStage | None = None,
        sort: str = "updated",
        direction: str = "desc",
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[TrackedApplication], int]:
        where, params = self._list_where(
            query=query,
            stage=stage,
        )
        sort_column = SORT_COLUMNS.get(sort, SORT_COLUMNS["updated"])
        sort_direction = "ASC" if direction == "asc" else "DESC"
        offset = (page - 1) * page_size
        list_query = f"""
            SELECT {APPLICATION_COLUMNS} FROM application_tracker {where}
            ORDER BY {sort_column} {sort_direction} NULLS LAST, public_id
            LIMIT %s OFFSET %s;
        """
        count_query = f"SELECT count(*) AS total FROM application_tracker {where};"
        async with self.pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(count_query, params)
                count_row = await cursor.fetchone() or {}
                await cursor.execute(list_query, [*params, page_size, offset])
                rows = await cursor.fetchall()
        return [TrackedApplication.model_validate(row) for row in rows], int(
            count_row.get("total", 0)
        )

    async def list_all_for_export(
        self,
        *,
        query: str | None = None,
        stage: ApplicationStage | None = None,
    ) -> list[TrackedApplication]:
        where, params = self._list_where(query=query, stage=stage)
        sql = f"""
            SELECT {APPLICATION_COLUMNS} FROM application_tracker {where}
            ORDER BY updated_at DESC, public_id;
        """
        async with self.pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql, params)
                rows = await cursor.fetchall()
        return [TrackedApplication.model_validate(row) for row in rows]

    async def get_application(self, public_id: UUID) -> TrackedApplication | None:
        query = f"SELECT {APPLICATION_COLUMNS} FROM application_tracker WHERE public_id = %s;"
        async with self.pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, (public_id,))
                row = await cursor.fetchone()
        return TrackedApplication.model_validate(row) if row else None

    async def get_application_for_public_job(
        self, job_id: UUID
    ) -> TrackedApplication | None:
        async with self.pool.connection() as connection:
            row = await (
                await connection.execute(
                    f"""SELECT {APPLICATION_COLUMNS} FROM application_tracker
                    WHERE job_id=%s AND archived_at IS NULL;""",
                    (job_id,),
                )
            ).fetchone()
        return TrackedApplication.model_validate(row) if row else None

    async def get_application_for_external_job(
        self, source: str, external_job_id: str
    ) -> TrackedApplication | None:
        async with self.pool.connection() as connection:
            row = await (
                await connection.execute(
                    f"""SELECT {APPLICATION_COLUMNS} FROM application_tracker
                    WHERE source_type=%s AND external_job_id=%s
                      AND archived_at IS NULL;""",
                    (source, external_job_id),
                )
            ).fetchone()
        return TrackedApplication.model_validate(row) if row else None

    async def list_tracked_job_states(self) -> list[TrackedJobState]:
        sql = """
            SELECT job_id, public_id AS application_id, stage
            FROM application_tracker
            WHERE job_id IS NOT NULL AND archived_at IS NULL;
        """
        async with self.pool.connection() as connection:
            result = await connection.execute(sql)
            rows = await result.fetchall()
        return [TrackedJobState.model_validate(row) for row in rows]

    async def list_tracked_external_states(
        self,
    ) -> list[dict[str, Any]]:
        """Map external source references to tracker records (WaterlooWorks)."""

        sql = """
            SELECT source_type AS source, external_job_id, public_id AS application_id, stage
            FROM application_tracker
            WHERE external_job_id IS NOT NULL AND archived_at IS NULL;
        """
        async with self.pool.connection() as connection:
            result = await connection.execute(sql)
            rows = await result.fetchall()
        return [
            {
                "source": row["source"],
                "external_job_id": row["external_job_id"],
                "application_id": row["application_id"],
                "stage": row["stage"],
            }
            for row in rows
        ]

    async def create_application(self, req: TrackerCreateRequest) -> TrackedApplication:
        now = datetime.now(UTC)
        applied_at = req.applied_at or (now if req.stage == ApplicationStage.APPLIED else None)
        interview_at = now if req.stage == ApplicationStage.INTERVIEW else None
        offer_at = now if req.stage == ApplicationStage.OFFER else None
        rejected_at = now if req.stage == ApplicationStage.REJECTED else None
        query = f"""
            INSERT INTO application_tracker (
                company_name, title, location_text, work_mode, job_url, job_description,
                application_deadline, salary_text, stage, applied_at,
                interview_at, offer_at, rejected_at,
                origin_type, source_type,
                created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s
            ) RETURNING {APPLICATION_COLUMNS};
        """
        values = (
            req.company_name.strip(),
            req.title.strip(),
            req.location_text,
            req.work_mode,
            req.job_url,
            req.job_description,
            req.application_deadline,
            req.salary_text,
            req.stage.value,
            applied_at,
            interview_at,
            offer_at,
            rejected_at,
            TrackerOrigin.CUSTOM.value,
            req.source.value,
            now,
            now,
        )
        stage_key = req.stage.value if req.stage else "interested"
        stage_title = application_stage_label(stage_key)
        async with self.pool.connection() as connection, connection.transaction():
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, values)
                row = await cursor.fetchone()
                await cursor.execute(
                    """
                        INSERT INTO application_tracker_events (
                            application_id, event_type, title, details, occurred_at
                        ) SELECT id, 'created', %s, %s, %s
                        FROM application_tracker WHERE public_id = %s;
                        """,
                    (
                        stage_title,
                        BOOKMARK_CREATED_DETAILS,
                        now,
                        row["id"],
                    ),
                )
        return TrackedApplication.model_validate(row)

    async def bookmark_job(self, job_id: UUID) -> TrackedApplication | None:
        """Create or restore one tracker record from canonical job data."""
        now = datetime.now(UTC)
        select_sql = """
            SELECT j.public_id, j.company_name, j.title, j.location_text,
                   j.work_mode, j.description, j.salary_min, j.salary_max,
                   j.salary_currency, j.salary_interval,
                   COALESCE(js.direct_url, js.source_url) AS job_url,
                   js.source AS job_source
            FROM jobs j
            LEFT JOIN LATERAL (
                SELECT source, source_url, direct_url
                FROM job_sources
                WHERE job_id = j.id
                ORDER BY first_seen_at, id
                LIMIT 1
            ) js ON true
            WHERE j.public_id = %s;
        """
        insert_sql = f"""
            INSERT INTO application_tracker (
                job_id, company_name, title, location_text, work_mode, job_url,
                job_description, salary_text, origin_type, source_type, stage,
                created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                'platform_bookmark', %s, 'interested', %s, %s
            )
            ON CONFLICT (job_id) WHERE job_id IS NOT NULL DO UPDATE SET
                company_name = EXCLUDED.company_name,
                title = EXCLUDED.title,
                location_text = EXCLUDED.location_text,
                work_mode = EXCLUDED.work_mode,
                job_url = EXCLUDED.job_url,
                job_description = EXCLUDED.job_description,
                salary_text = EXCLUDED.salary_text,
                origin_type = 'platform_bookmark',
                source_type = EXCLUDED.source_type,
                archived_at = NULL,
                updated_at = EXCLUDED.updated_at
            RETURNING {APPLICATION_COLUMNS};
        """
        async with self.pool.connection() as connection, connection.transaction():
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(select_sql, (job_id,))
                source_job = await cursor.fetchone()
                if not source_job:
                    return None
                source_type = {
                    "linkedin": "linkedin",
                    "indeed": "indeed",
                    "glassdoor": "glassdoor",
                    "zip_recruiter": "zip_recruiter",
                    "ziprecruiter": "zip_recruiter",
                    "google": "google",
                    "google_jobs": "google",
                    "waterloo_work": "waterloo_work",
                    "waterlooworks": "waterloo_work",
                }.get((source_job["job_source"] or "").lower(), "wecanfindintern")
                salary_text = format_salary_text(
                    source_job["salary_min"],
                    source_job["salary_max"],
                    currency=source_job["salary_currency"],
                    interval=source_job["salary_interval"],
                )
                await cursor.execute(
                    insert_sql,
                    (
                        source_job["public_id"],
                        source_job["company_name"] or "Company not specified",
                        source_job["title"],
                        source_job["location_text"],
                        source_job["work_mode"],
                        source_job["job_url"],
                        source_job["description"],
                        salary_text,
                        source_type,
                        now,
                        now,
                    ),
                )
                row = await cursor.fetchone()
                if row:
                    await self._record_bookmark_created_event(cursor, row["id"], now)
        return TrackedApplication.model_validate(row) if row else None

    async def unbookmark_job(self, job_id: UUID) -> tuple[bool, str | None]:
        """Safely remove a bookmarked job if it is still in 'interested' stage.

        Returns:
            (True, None) if successfully deleted.
            (False, current_stage) if the application is protected in an active stage.
            (False, None) if the record was not found.
        """
        return await self._unbookmark(
            reference_predicate="job_id = %s",
            parameters=(job_id,),
        )

    async def bookmark_waterlooworks_job(
        self,
        *,
        source_job_id: str,
        company_name: str,
        title: str,
        location_text: str | None = None,
        work_mode: str | None = None,
        job_url: str | None = None,
        job_description: str | None = None,
        application_deadline: date | None = None,
        salary_text: str | None = None,
    ) -> TrackedApplication | None:
        """Create or restore one tracker record referencing a WaterlooWorks job."""

        now = datetime.now(UTC)
        sql = f"""
            INSERT INTO application_tracker (
                external_job_id, source_type, company_name, title, location_text,
                work_mode, job_url, job_description, application_deadline, salary_text,
                origin_type, stage, created_at, updated_at
            ) VALUES (
                %s, 'waterloo_work', %s, %s, %s, %s, %s, %s, %s, %s,
                'platform_bookmark', 'interested', %s, %s
            )
            ON CONFLICT (source_type, external_job_id)
                WHERE external_job_id IS NOT NULL
            DO UPDATE SET
                company_name = EXCLUDED.company_name,
                title = EXCLUDED.title,
                location_text = EXCLUDED.location_text,
                work_mode = EXCLUDED.work_mode,
                job_url = EXCLUDED.job_url,
                job_description = EXCLUDED.job_description,
                application_deadline = EXCLUDED.application_deadline,
                salary_text = EXCLUDED.salary_text,
                origin_type = 'platform_bookmark',
                archived_at = NULL,
                updated_at = EXCLUDED.updated_at
            RETURNING {APPLICATION_COLUMNS};
        """
        async with self.pool.connection() as connection, connection.transaction():
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    sql,
                    (
                        source_job_id,
                        company_name,
                        title,
                        location_text,
                        work_mode,
                        job_url,
                        job_description,
                        application_deadline,
                        salary_text,
                        now,
                        now,
                    ),
                )
                row = await cursor.fetchone()
                if row:
                    await self._record_bookmark_created_event(cursor, row["id"], now)
        return TrackedApplication.model_validate(row) if row else None

    async def unbookmark_waterlooworks_job(
        self, source_job_id: str
    ) -> tuple[bool, str | None]:
        """Safely remove a WaterlooWorks-tracked job if still 'interested'."""

        return await self._unbookmark(
            reference_predicate=(
                "external_job_id = %s AND source_type = 'waterloo_work'"
            ),
            parameters=(source_job_id,),
        )

    async def sync_waterlooworks_application(
        self,
        *,
        source_job_id: str,
        company_name: str,
        title: str,
        stage: ApplicationStage,
        waterlooworks_status: str,
        submitted_at: datetime | None = None,
        location_text: str | None = None,
        work_mode: str | None = None,
        job_url: str | None = None,
        job_description: str | None = None,
        application_deadline: date | None = None,
        salary_text: str | None = None,
    ) -> TrackedApplication:
        """Mirror source-owned fields without overriding user workflow state."""

        now = datetime.now(UTC)
        applied_at = submitted_at or now
        interview_at = now if stage == ApplicationStage.INTERVIEW else None
        offer_at = now if stage == ApplicationStage.OFFER else None
        rejected_at = now if stage == ApplicationStage.REJECTED else None
        stage_title = application_stage_label(stage)
        async with self.pool.connection() as connection, connection.transaction():
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    """
                    SELECT public_id, stage, external_stage, external_status
                    FROM application_tracker
                    WHERE source_type='waterloo_work' AND external_job_id=%s
                    FOR UPDATE
                    """,
                    (source_job_id,),
                )
                previous = await cursor.fetchone()
                if previous:
                    await cursor.execute(
                        f"""
                        UPDATE application_tracker SET
                            company_name=%s, title=%s, location_text=%s, work_mode=%s,
                            job_url=COALESCE(NULLIF(%s, ''), job_url),
                            job_description=COALESCE(NULLIF(%s, ''), job_description),
                            application_deadline=COALESCE(%s, application_deadline),
                            salary_text=COALESCE(%s, salary_text),
                            origin_type='platform_bookmark',
                            external_stage=%s, external_status=%s,
                            applied_at=COALESCE(applied_at, %s),
                            updated_at=%s
                        WHERE public_id=%s
                        RETURNING {APPLICATION_COLUMNS}
                        """,
                        (
                            company_name,
                            title,
                            location_text,
                            work_mode,
                            job_url,
                            job_description,
                            application_deadline,
                            salary_text,
                            stage.value,
                            waterlooworks_status,
                            applied_at,
                            now,
                            previous["public_id"],
                        ),
                    )
                    row = await cursor.fetchone()
                    if previous["external_status"] != waterlooworks_status:
                        old_status = previous["external_status"] or "Not previously synced"
                        await cursor.execute(
                            """
                            INSERT INTO application_tracker_events(
                                application_id, event_type, title, details, occurred_at
                            ) SELECT id, 'external_status', %s, %s, %s
                            FROM application_tracker WHERE public_id=%s
                            """,
                            (
                                "WaterlooWorks status updated",
                                f"{old_status} → {waterlooworks_status}",
                                now,
                                previous["public_id"],
                            ),
                        )
                else:
                    await cursor.execute(
                        f"""
                        INSERT INTO application_tracker(
                            external_job_id, source_type, company_name, title,
                            location_text, work_mode, job_url, job_description,
                            application_deadline, salary_text, origin_type, stage,
                            external_stage, external_status,
                            applied_at, interview_at, offer_at, rejected_at,
                            created_at, updated_at
                        ) VALUES (
                            %s, 'waterloo_work', %s, %s, %s, %s, %s, %s, %s, %s,
                            'platform_bookmark', %s, %s, %s, %s, %s, %s, %s, %s, %s
                        ) RETURNING {APPLICATION_COLUMNS}
                        """,
                        (
                            source_job_id,
                            company_name,
                            title,
                            location_text,
                            work_mode,
                            job_url,
                            job_description,
                            application_deadline,
                            salary_text,
                            stage.value,
                            stage.value,
                            waterlooworks_status,
                            applied_at,
                            interview_at,
                            offer_at,
                            rejected_at,
                            now,
                            now,
                        ),
                    )
                    row = await cursor.fetchone()
                    await cursor.execute(
                        """
                        INSERT INTO application_tracker_events(
                            application_id, event_type, title, details, occurred_at
                        ) SELECT id, 'created', %s, %s, %s
                        FROM application_tracker WHERE public_id=%s
                        """,
                        (
                            stage_title,
                            f"Imported from WaterlooWorks application status: "
                            f"{waterlooworks_status}",
                            submitted_at or now,
                            row["id"],
                        ),
                    )
        return TrackedApplication.model_validate(row)

    async def update_application(
        self, public_id: UUID, req: TrackerUpdateRequest
    ) -> TrackedApplication | None:
        now = datetime.now(UTC)
        scalar_fields = (
            "company_name",
            "title",
            "location_text",
            "work_mode",
            "job_url",
            "job_description",
            "application_deadline",
            "salary_text",
            "applied_at",
            "source",
        )
        async with self.pool.connection() as connection, connection.transaction():
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    "SELECT stage, origin_type FROM application_tracker WHERE public_id = %s;",
                    (public_id,),
                )
                previous = await cursor.fetchone()
                if not previous:
                    return None
                if previous["origin_type"] == TrackerOrigin.PLATFORM_BOOKMARK.value:
                    allowed = {"stage", "applied_at"}
                    forbidden = req.model_fields_set - allowed
                    if forbidden:
                        fields = ", ".join(sorted(forbidden))
                        raise ValueError(f"Platform job fields are read-only: {fields}")

                updates: list[str] = ["updated_at = %s"]
                params: list[Any] = [now]
                for field in scalar_fields:
                    value = getattr(req, field)
                    if field in req.model_fields_set:
                        column = "source_type" if field == "source" else field
                        updates.append(f"{column} = %s")
                        if field == "source" and value is not None:
                            value = value.value
                        params.append(
                            value.strip()
                            if field in {"company_name", "title"} and value
                            else value
                        )
                if req.stage is not None:
                    updates.append("stage = %s")
                    params.append(req.stage.value)
                    timestamp_field = {
                        ApplicationStage.APPLIED: "applied_at",
                        ApplicationStage.INTERVIEW: "interview_at",
                        ApplicationStage.OFFER: "offer_at",
                        ApplicationStage.REJECTED: "rejected_at",
                    }.get(req.stage)
                    if timestamp_field and timestamp_field not in req.model_fields_set:
                        updates.append(f"{timestamp_field} = COALESCE({timestamp_field}, %s)")
                        params.append(now)
                params.append(public_id)
                query = f"""
                    UPDATE application_tracker SET {", ".join(updates)}
                    WHERE public_id = %s RETURNING {APPLICATION_COLUMNS};
                """
                await cursor.execute(query, params)
                row = await cursor.fetchone()
                if req.stage is not None and previous["stage"] != req.stage.value:
                    prev_title = application_stage_label(previous["stage"])
                    new_title = application_stage_label(req.stage)
                    occurred = now
                    if req.stage == ApplicationStage.APPLIED and req.applied_at:
                        occurred = req.applied_at
                    await cursor.execute(
                        """
                            INSERT INTO application_tracker_events (
                                application_id, event_type, title, details, occurred_at
                            ) SELECT id, 'stage_change', %s, %s, %s
                            FROM application_tracker WHERE public_id = %s;
                            """,
                        (
                            new_title,
                            f"{prev_title} → {new_title}",
                            occurred,
                            public_id,
                        ),
                    )
        return TrackedApplication.model_validate(row) if row else None

    async def bulk_update(
        self,
        ids: list[UUID],
        *,
        stage: ApplicationStage | None = None,
    ) -> int:
        updates = ["updated_at = now()"]
        params: list[Any] = []
        if stage:
            updates.append("stage = %s")
            params.append(stage.value)
            timestamp_field = {
                ApplicationStage.APPLIED: "applied_at",
                ApplicationStage.INTERVIEW: "interview_at",
                ApplicationStage.OFFER: "offer_at",
                ApplicationStage.REJECTED: "rejected_at",
            }.get(stage)
            if timestamp_field:
                updates.append(f"{timestamp_field} = COALESCE({timestamp_field}, now())")
        params.append(ids)
        where = "public_id = ANY(%s)"
        if stage:
            where += " AND stage IS DISTINCT FROM %s"
            params.append(stage.value)
        sql = f"UPDATE application_tracker SET {', '.join(updates)} WHERE {where};"
        async with self.pool.connection() as connection, connection.transaction():
            if stage:
                await connection.execute(
                    f"""
                    INSERT INTO application_tracker_events (
                        application_id, event_type, title, details, occurred_at
                    )
                    SELECT id, 'stage_change',
                        {_stage_title_sql('%s')},
                        {_stage_title_sql('application_tracker.stage')} || ' → ' ||
                        {_stage_title_sql('%s')},
                        now()
                    FROM application_tracker
                    WHERE public_id = ANY(%s)
                      AND application_tracker.stage IS DISTINCT FROM %s;
                    """,
                    (
                        stage.value,
                        stage.value,
                        stage.value,
                        stage.value,
                        ids,
                        stage.value,
                    ),
                )
            result = await connection.execute(sql, params)
        return result.rowcount or 0

    async def bulk_delete(self, ids: list[UUID]) -> int:
        async with self.pool.connection() as connection:
            result = await connection.execute(
                "DELETE FROM application_tracker WHERE public_id = ANY(%s);", (ids,)
            )
        return result.rowcount or 0

    async def delete_application(self, public_id: UUID) -> bool:
        async with self.pool.connection() as connection:
            result = await connection.execute(
                "DELETE FROM application_tracker WHERE public_id = %s;", (public_id,)
            )
        return (result.rowcount or 0) > 0

    async def get_stats(self) -> TrackerStatsResponse:
        query = """
            SELECT
                count(*) FILTER (WHERE archived_at IS NULL) AS total,
                count(*) FILTER (WHERE stage = 'interested' AND archived_at IS NULL) AS interested,
                count(*) FILTER (WHERE stage = 'applied' AND archived_at IS NULL) AS applied,
                count(*) FILTER (WHERE stage = 'interview' AND archived_at IS NULL) AS interview,
                count(*) FILTER (WHERE stage = 'offer' AND archived_at IS NULL) AS offer,
                count(*) FILTER (WHERE stage = 'rejected' AND archived_at IS NULL) AS rejected,
                count(*) FILTER (
                    WHERE archived_at IS NULL
                      AND (
                          stage <> 'interested'
                          OR applied_at IS NOT NULL
                          OR interview_at IS NOT NULL
                          OR offer_at IS NOT NULL
                      )
                ) AS submitted,
                count(*) FILTER (
                    WHERE archived_at IS NULL
                      AND (interview_at IS NOT NULL OR offer_at IS NOT NULL)
                ) AS responded
            FROM application_tracker;
        """
        async with self.pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query)
                row = await cursor.fetchone() or {}
        applied = int(row.get("applied", 0))
        interview = int(row.get("interview", 0))
        offer = int(row.get("offer", 0))
        rejected = int(row.get("rejected", 0))
        responded = int(row.get("responded", 0))
        submitted = int(row.get("submitted", 0))
        return TrackerStatsResponse(
            total=int(row.get("total", 0)),
            interested_count=int(row.get("interested", 0)),
            applied_count=applied,
            interview_count=interview,
            offer_count=offer,
            rejected_count=rejected,
            response_rate_percent=round(responded / submitted * 100, 1)
            if submitted
            else 0.0,
        )

    async def list_events(self, public_id: UUID) -> list[TrackerEvent]:
        ensure_sql = f"""
            INSERT INTO application_tracker_events (
                application_id, event_type, title, details, occurred_at
            )
            SELECT a.id, 'created',
                {_stage_title_sql('a.stage')},
                %s,
                COALESCE(a.created_at, now())
            FROM application_tracker a
            WHERE a.public_id = %s
            AND NOT EXISTS (
                SELECT 1 FROM application_tracker_events
                WHERE application_id = a.id
            );
        """
        sql = """
            SELECT e.public_id AS id, a.public_id AS application_id, e.event_type,
                e.title, e.details, e.occurred_at, e.created_at
            FROM application_tracker_events e
            JOIN application_tracker a ON a.id = e.application_id
            WHERE a.public_id = %s ORDER BY e.occurred_at DESC, e.id DESC;
        """
        async with self.pool.connection() as connection, connection.transaction():
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(ensure_sql, (BOOKMARK_CREATED_DETAILS, public_id))
                await cursor.execute(sql, (public_id,))
                rows = await cursor.fetchall()
        return [TrackerEvent.model_validate(row) for row in rows]
