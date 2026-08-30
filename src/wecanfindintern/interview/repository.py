"""Persistence for mock-interview practice sessions and analyzed answers."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

SESSION_LIST_LIMIT = 30


class InterviewRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def create_session(
        self,
        *,
        job_description: str,
        provider: str,
        model_name: str,
        questions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        async with self.pool.connection() as connection, connection.cursor(
            row_factory=dict_row
        ) as cursor:
            await cursor.execute(
                """
                INSERT INTO interview_sessions (job_description, provider, model_name, questions)
                VALUES (%s, %s, %s, %s)
                RETURNING id, job_description, provider, model_name, questions,
                          created_at, updated_at
                """,
                (
                    job_description,
                    provider,
                    model_name,
                    json.dumps(questions, ensure_ascii=False),
                ),
            )
            return await cursor.fetchone()

    async def get_session(self, session_id: UUID) -> dict[str, Any] | None:
        async with self.pool.connection() as connection, connection.cursor(
            row_factory=dict_row
        ) as cursor:
            await cursor.execute(
                """
                SELECT id, job_description, provider, model_name, questions,
                       created_at, updated_at
                FROM interview_sessions WHERE id = %s
                """,
                (session_id,),
            )
            session = await cursor.fetchone()
            if session is None:
                return None
            await cursor.execute(
                """
                SELECT id, question_index, question_text, answer_text, transcript,
                       transcript_language, duration_seconds, score, summary,
                       star_feedback, timeline, advice, provider, model_name, created_at
                FROM interview_answers
                WHERE session_id = %s
                ORDER BY question_index
                """,
                (session_id,),
            )
            answers = await cursor.fetchall()
        session["answers"] = answers
        return session

    async def list_sessions(self, *, limit: int = SESSION_LIST_LIMIT) -> list[dict[str, Any]]:
        async with self.pool.connection() as connection, connection.cursor(
            row_factory=dict_row
        ) as cursor:
            await cursor.execute(
                """
                SELECT s.id, s.provider, s.model_name, s.created_at,
                       coalesce(jsonb_array_length(s.questions), 0) AS question_count,
                       count(a.id) AS answer_count,
                       coalesce(round(avg(a.score)), 0)::int AS avg_score,
                       max(a.updated_at) AS last_practiced_at
                FROM interview_sessions s
                LEFT JOIN interview_answers a ON a.session_id = s.id
                GROUP BY s.id
                ORDER BY s.created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return await cursor.fetchall()

    async def delete_session(self, session_id: UUID) -> bool:
        async with self.pool.connection() as connection, connection.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM interview_sessions WHERE id = %s", (session_id,)
            )
            return cursor.rowcount > 0

    async def upsert_answer(
        self,
        *,
        session_id: UUID,
        question_index: int,
        question_text: str,
        answer_text: str,
        transcript: str,
        transcript_language: str,
        duration_seconds: float,
        score: int,
        summary: str,
        star_feedback: str,
        timeline: list[dict[str, Any]],
        advice: list[str],
        provider: str,
        model_name: str,
    ) -> None:
        async with self.pool.connection() as connection, connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO interview_answers (
                    session_id, question_index, question_text, answer_text, transcript,
                    transcript_language, duration_seconds, score, summary, star_feedback,
                    timeline, advice, provider, model_name
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_id, question_index) DO UPDATE SET
                    question_text = EXCLUDED.question_text,
                    answer_text = EXCLUDED.answer_text,
                    transcript = EXCLUDED.transcript,
                    transcript_language = EXCLUDED.transcript_language,
                    duration_seconds = EXCLUDED.duration_seconds,
                    score = EXCLUDED.score,
                    summary = EXCLUDED.summary,
                    star_feedback = EXCLUDED.star_feedback,
                    timeline = EXCLUDED.timeline,
                    advice = EXCLUDED.advice,
                    provider = EXCLUDED.provider,
                    model_name = EXCLUDED.model_name,
                    updated_at = now()
                """,
                (
                    session_id,
                    question_index,
                    question_text,
                    answer_text,
                    transcript,
                    transcript_language,
                    duration_seconds,
                    score,
                    summary,
                    star_feedback,
                    json.dumps(timeline, ensure_ascii=False),
                    json.dumps(advice, ensure_ascii=False),
                    provider,
                    model_name,
                ),
            )
            await connection.execute(
                "UPDATE interview_sessions SET updated_at = now() WHERE id = %s",
                (session_id,),
            )

    async def practice_trend(self) -> dict[str, Any]:
        """Score trend across sessions for the progress panel."""

        async with self.pool.connection() as connection, connection.cursor(
            row_factory=dict_row
        ) as cursor:
            await cursor.execute(
                """
                SELECT s.id, s.created_at,
                       coalesce(round(avg(a.score)), 0)::int AS avg_score,
                       count(a.id) AS answer_count
                FROM interview_sessions s
                LEFT JOIN interview_answers a ON a.session_id = s.id
                GROUP BY s.id
                ORDER BY s.created_at
                """
            )
            rows = await cursor.fetchall()
        return summarize_trend(rows)


def summarize_trend(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure aggregation so the trend math is unit-testable."""

    scored = [row for row in rows if row["answer_count"] > 0]
    scores = [int(row["avg_score"]) for row in scored]
    first = scores[0] if scores else 0
    latest = scores[-1] if scores else 0
    return {
        "session_count": len(rows),
        "answered_sessions": len(scored),
        "answer_count": int(sum(int(row["answer_count"]) for row in rows)),
        "average_score": int(round(sum(scores) / len(scores))) if scores else 0,
        "first_session_score": first,
        "latest_session_score": latest,
        "improvement": latest - first,
        "trend": [
            {
                "session_id": str(row["id"]),
                "created_at": row["created_at"].isoformat(),
                "avg_score": int(row["avg_score"]),
                "answer_count": int(row["answer_count"]),
            }
            for row in rows
        ],
    }
