"""PostgreSQL repository for Application Tracker."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from wecanfindintern.tracker.models import (
    ApplicationStage,
    TrackedApplication,
    TrackerCreateRequest,
    TrackerStatsResponse,
    TrackerUpdateRequest,
)


class TrackerRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def list_applications(self) -> list[TrackedApplication]:
        query = """
            SELECT 
                public_id AS id,
                job_id,
                company_name,
                title,
                location_text,
                work_mode,
                job_url,
                salary_text,
                stage,
                notes,
                applied_at,
                interview_at,
                offer_at,
                rejected_at,
                created_at,
                updated_at
            FROM application_tracker
            ORDER BY updated_at DESC;
        """
        async with self.pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query)
                rows = await cursor.fetchall()
                return [TrackedApplication.model_validate(row) for row in rows]

    async def get_application(self, public_id: UUID) -> TrackedApplication | None:
        query = """
            SELECT 
                public_id AS id,
                job_id,
                company_name,
                title,
                location_text,
                work_mode,
                job_url,
                salary_text,
                stage,
                notes,
                applied_at,
                interview_at,
                offer_at,
                rejected_at,
                created_at,
                updated_at
            FROM application_tracker
            WHERE public_id = %s;
        """
        async with self.pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, (public_id,))
                row = await cursor.fetchone()
                return TrackedApplication.model_validate(row) if row else None

    async def create_application(self, req: TrackerCreateRequest) -> TrackedApplication:
        now = datetime.now(UTC)
        applied_at = now if req.stage == ApplicationStage.APPLIED else None
        interview_at = now if req.stage == ApplicationStage.INTERVIEW else None
        offer_at = now if req.stage == ApplicationStage.OFFER else None
        rejected_at = now if req.stage == ApplicationStage.REJECTED else None

        # Check if job_id already tracked, if so update or return
        query = """
            INSERT INTO application_tracker (
                job_id, company_name, title, location_text, work_mode, job_url,
                salary_text, stage, notes, applied_at, interview_at, offer_at, rejected_at,
                created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING 
                public_id AS id, job_id, company_name, title, location_text, work_mode,
                job_url, salary_text, stage, notes, applied_at, interview_at, offer_at,
                rejected_at, created_at, updated_at;
        """
        async with self.pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    query,
                    (
                        req.job_id,
                        req.company_name.strip(),
                        req.title.strip(),
                        req.location_text,
                        req.work_mode,
                        req.job_url,
                        req.salary_text,
                        req.stage.value,
                        req.notes,
                        applied_at,
                        interview_at,
                        offer_at,
                        rejected_at,
                        now,
                        now,
                    ),
                )
                row = await cursor.fetchone()
                return TrackedApplication.model_validate(row)

    async def update_application(
        self, public_id: UUID, req: TrackerUpdateRequest
    ) -> TrackedApplication | None:
        now = datetime.now(UTC)
        updates: list[str] = ["updated_at = %s"]
        params: list[Any] = [now]

        if req.company_name is not None:
            updates.append("company_name = %s")
            params.append(req.company_name.strip())
        if req.title is not None:
            updates.append("title = %s")
            params.append(req.title.strip())
        if req.location_text is not None:
            updates.append("location_text = %s")
            params.append(req.location_text)
        if req.work_mode is not None:
            updates.append("work_mode = %s")
            params.append(req.work_mode)
        if req.job_url is not None:
            updates.append("job_url = %s")
            params.append(req.job_url)
        if req.salary_text is not None:
            updates.append("salary_text = %s")
            params.append(req.salary_text)
        if req.notes is not None:
            updates.append("notes = %s")
            params.append(req.notes)

        if req.stage is not None:
            updates.append("stage = %s")
            params.append(req.stage.value)
            if req.stage == ApplicationStage.APPLIED:
                updates.append("applied_at = COALESCE(applied_at, %s)")
                params.append(now)
            elif req.stage == ApplicationStage.INTERVIEW:
                updates.append("interview_at = COALESCE(interview_at, %s)")
                params.append(now)
            elif req.stage == ApplicationStage.OFFER:
                updates.append("offer_at = COALESCE(offer_at, %s)")
                params.append(now)
            elif req.stage == ApplicationStage.REJECTED:
                updates.append("rejected_at = COALESCE(rejected_at, %s)")
                params.append(now)

        if req.applied_at is not None:
            updates.append("applied_at = %s")
            params.append(req.applied_at)
        if req.interview_at is not None:
            updates.append("interview_at = %s")
            params.append(req.interview_at)
        if req.offer_at is not None:
            updates.append("offer_at = %s")
            params.append(req.offer_at)
        if req.rejected_at is not None:
            updates.append("rejected_at = %s")
            params.append(req.rejected_at)

        params.append(public_id)
        query = f"""
            UPDATE application_tracker
            SET {", ".join(updates)}
            WHERE public_id = %s
            RETURNING 
                public_id AS id, job_id, company_name, title, location_text, work_mode,
                job_url, salary_text, stage, notes, applied_at, interview_at, offer_at,
                rejected_at, created_at, updated_at;
        """
        async with self.pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, params)
                row = await cursor.fetchone()
                return TrackedApplication.model_validate(row) if row else None

    async def delete_application(self, public_id: UUID) -> bool:
        query = "DELETE FROM application_tracker WHERE public_id = %s;"
        async with self.pool.connection() as connection:
            result = await connection.execute(query, (public_id,))
            return (result.rowcount or 0) > 0

    async def get_stats(self) -> TrackerStatsResponse:
        query = """
            SELECT
                count(*) AS total,
                count(*) FILTER (WHERE stage = 'interested') AS interested_count,
                count(*) FILTER (WHERE stage = 'applied') AS applied_count,
                count(*) FILTER (WHERE stage = 'interview') AS interview_count,
                count(*) FILTER (WHERE stage = 'offer') AS offer_count,
                count(*) FILTER (WHERE stage = 'rejected') AS rejected_count
            FROM application_tracker;
        """
        async with self.pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query)
                row = await cursor.fetchone() or {}
                total = int(row.get("total", 0))
                interested = int(row.get("interested_count", 0))
                applied = int(row.get("applied_count", 0))
                interview = int(row.get("interview_count", 0))
                offer = int(row.get("offer_count", 0))
                rejected = int(row.get("rejected_count", 0))

                submitted_or_beyond = applied + interview + offer + rejected
                positive_responses = interview + offer
                rate = round((positive_responses / submitted_or_beyond * 100), 1) if submitted_or_beyond > 0 else 0.0

                return TrackerStatsResponse(
                    total=total,
                    interested_count=interested,
                    applied_count=applied,
                    interview_count=interview,
                    offer_count=offer,
                    rejected_count=rejected,
                    response_rate_percent=rate,
                )
