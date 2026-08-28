"""PostgreSQL repository for the application tracker workspace."""

# ruff: noqa: SIM117

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from wecanfindintern.tracker.models import (
    ApplicationStage,
    TrackedApplication,
    TrackedJobState,
    TrackerCreateRequest,
    TrackerEvent,
    TrackerOrigin,
    TrackerStatsResponse,
    TrackerUpdateRequest,
)

APPLICATION_COLUMNS = """
    public_id AS id, job_id, company_name, title, location_text, work_mode,
    job_url, job_description, salary_text, origin_type, source_type AS source, stage,
    applied_at, created_at, updated_at
"""

SORT_COLUMNS = {
    "updated": "updated_at",
    "created": "created_at",
    "applied": "applied_at",
    "company": "lower(company_name)",
}

STAGE_TITLES: dict[str, str] = {
    "interested": "Interested",
    "applied": "Applied",
    "interview": "Interview",
    "offer": "Offer",
    "rejected": "Refused",
}


class TrackerRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

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

    async def create_application(self, req: TrackerCreateRequest) -> TrackedApplication:
        now = datetime.now(UTC)
        applied_at = req.applied_at or (now if req.stage == ApplicationStage.APPLIED else None)
        interview_at = now if req.stage == ApplicationStage.INTERVIEW else None
        offer_at = now if req.stage == ApplicationStage.OFFER else None
        rejected_at = now if req.stage == ApplicationStage.REJECTED else None
        query = f"""
            INSERT INTO application_tracker (
                company_name, title, location_text, work_mode, job_url, job_description,
                salary_text, stage, applied_at, interview_at, offer_at, rejected_at,
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
        stage_title = STAGE_TITLES.get(stage_key, stage_key.title())
        async with self.pool.connection() as connection, connection.transaction():
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, values)
                row = await cursor.fetchone()
                await cursor.execute(
                    """
                        INSERT INTO application_tracker_events (
                            application_id, event_type, title, details, occurred_at
                        ) SELECT id, 'created', %s, 'Opportunity saved to pipeline', %s
                        FROM application_tracker WHERE public_id = %s;
                        """,
                    (
                        stage_title,
                        now,
                        row["id"],
                    ),
                )
        return TrackedApplication.model_validate(row)

    async def bookmark_job(self, job_id: UUID) -> TrackedApplication | None:
        """Create or restore one tracker record from canonical job data."""
        now = datetime.now(UTC)
        sql = f"""
            INSERT INTO application_tracker (
                job_id, company_name, title, location_text, work_mode, job_url,
                job_description, salary_text, origin_type, source_type, stage,
                created_at, updated_at
            )
            SELECT
                j.public_id,
                COALESCE(NULLIF(j.company_name, ''), 'Company not specified'),
                j.title,
                j.location_text,
                j.work_mode,
                COALESCE(js.direct_url, js.source_url),
                j.description,
                CASE
                    WHEN j.salary_min IS NULL AND j.salary_max IS NULL THEN NULL
                    ELSE concat_ws(' ', j.salary_currency,
                        CASE
                            WHEN j.salary_min IS NOT NULL AND j.salary_max IS NOT NULL
                                THEN trim(to_char(j.salary_min, 'FM999999990.00'))
                                    || '–'
                                    || trim(to_char(j.salary_max, 'FM999999990.00'))
                            WHEN j.salary_min IS NOT NULL
                                THEN 'from ' || trim(to_char(j.salary_min, 'FM999999990.00'))
                            ELSE 'up to ' || trim(to_char(j.salary_max, 'FM999999990.00'))
                        END,
                        CASE WHEN j.salary_interval IS NOT NULL THEN '/' || j.salary_interval END)
                END,
                'platform_bookmark',
                CASE lower(COALESCE(js.source, ''))
                    WHEN 'linkedin' THEN 'linkedin'
                    WHEN 'indeed' THEN 'indeed'
                    WHEN 'waterloo_work' THEN 'waterloo_work'
                    WHEN 'waterlooworks' THEN 'waterloo_work'
                    ELSE 'wecanfindintern'
                END,
                'interested', %s, %s
            FROM jobs j
            LEFT JOIN LATERAL (
                SELECT source, source_url, direct_url
                FROM job_sources
                WHERE job_id = j.id
                ORDER BY first_seen_at, id
                LIMIT 1
            ) js ON true
            WHERE j.public_id = %s
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
                await cursor.execute(sql, (now, now, job_id))
                row = await cursor.fetchone()
                if row:
                    await cursor.execute(
                        """
                        INSERT INTO application_tracker_events (
                            application_id, event_type, title, details, occurred_at
                        )
                        SELECT id, 'created', 'Interested', 'Opportunity saved to pipeline', %s
                        FROM application_tracker WHERE public_id = %s
                        AND NOT EXISTS (
                            SELECT 1 FROM application_tracker_events
                            WHERE application_id = application_tracker.id AND event_type = 'created'
                        );
                        """,
                        (now, row["id"]),
                    )
        return TrackedApplication.model_validate(row) if row else None

    async def unbookmark_job(self, job_id: UUID) -> tuple[bool, str | None]:
        """Safely remove a bookmarked job if it is still in 'interested' stage.

        Returns:
            (True, None) if successfully deleted.
            (False, current_stage) if the application is protected in an active stage.
            (False, None) if the record was not found.
        """
        async with self.pool.connection() as connection, connection.transaction():
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    "SELECT id, stage FROM application_tracker WHERE job_id = %s;",
                    (job_id,),
                )
                row = await cursor.fetchone()
                if not row:
                    return False, None
                stage = row["stage"]
                if stage != "interested":
                    return False, stage
                await cursor.execute(
                    "DELETE FROM application_tracker WHERE job_id = %s;",
                    (job_id,),
                )
                return True, None

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
                if req.stage is not None:
                    prev_title = STAGE_TITLES.get(previous["stage"], previous["stage"].title())
                    new_title = STAGE_TITLES.get(req.stage.value, req.stage.value.title())
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
                            (
                                f"{prev_title} → {new_title}"
                                if previous["stage"] != req.stage.value
                                else f"{new_title} recorded"
                            ),
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
        sql = f"UPDATE application_tracker SET {', '.join(updates)} WHERE public_id = ANY(%s);"
        async with self.pool.connection() as connection, connection.transaction():
            if stage:
                await connection.execute(
                    """
                    INSERT INTO application_tracker_events (
                        application_id, event_type, title, details, occurred_at
                    )
                    SELECT id, 'stage_change',
                        CASE %s
                            WHEN 'interested' THEN 'Interested'
                            WHEN 'applied' THEN 'Applied'
                            WHEN 'interview' THEN 'Interview'
                            WHEN 'offer' THEN 'Offer'
                            WHEN 'rejected' THEN 'Refused'
                            ELSE initcap(%s)
                        END,
                        CASE application_tracker.stage
                            WHEN 'interested' THEN 'Interested'
                            WHEN 'applied' THEN 'Applied'
                            WHEN 'interview' THEN 'Interview'
                            WHEN 'offer' THEN 'Offer'
                            WHEN 'rejected' THEN 'Refused'
                            ELSE initcap(application_tracker.stage)
                        END || ' → ' ||
                        CASE %s
                            WHEN 'interested' THEN 'Interested'
                            WHEN 'applied' THEN 'Applied'
                            WHEN 'interview' THEN 'Interview'
                            WHEN 'offer' THEN 'Offer'
                            WHEN 'rejected' THEN 'Refused'
                            ELSE initcap(%s)
                        END,
                        now()
                    FROM application_tracker
                    WHERE public_id = ANY(%s);
                    """,
                    (stage.value, stage.value, stage.value, stage.value, ids),
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
                count(*) FILTER (WHERE stage = 'rejected' AND archived_at IS NULL) AS rejected
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
        submitted = applied + interview + offer + rejected
        return TrackerStatsResponse(
            total=int(row.get("total", 0)),
            interested_count=int(row.get("interested", 0)),
            applied_count=applied,
            interview_count=interview,
            offer_count=offer,
            rejected_count=rejected,
            response_rate_percent=round((interview + offer) / submitted * 100, 1)
            if submitted
            else 0.0,
        )

    async def list_events(self, public_id: UUID) -> list[TrackerEvent]:
        ensure_sql = """
            INSERT INTO application_tracker_events (
                application_id, event_type, title, details, occurred_at
            )
            SELECT a.id, 'created',
                CASE a.stage
                    WHEN 'interested' THEN 'Interested'
                    WHEN 'applied' THEN 'Applied'
                    WHEN 'interview' THEN 'Interview'
                    WHEN 'offer' THEN 'Offer'
                    WHEN 'rejected' THEN 'Refused'
                    ELSE initcap(a.stage)
                END,
                'Opportunity saved to pipeline',
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
                await cursor.execute(ensure_sql, (public_id,))
                await cursor.execute(sql, (public_id,))
                rows = await cursor.fetchall()
        return [TrackerEvent.model_validate(row) for row in rows]
