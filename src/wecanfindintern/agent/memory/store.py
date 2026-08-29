"""PostgreSQL persistence for agent memory (summaries, memories, prefs)."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from wecanfindintern.agent.memory.models import (
    MEMORY_STATUS_ACTIVE,
    MEMORY_STATUS_EXPIRED,
    MEMORY_STATUS_SUPERSEDED,
    ConversationSummary,
    MemoryMessage,
    MemoryRecord,
    SessionMemoryState,
)


def memory_content_hash(content: str) -> str:
    normalized = " ".join(content.strip().lower().split())
    return sha256(normalized.encode("utf-8")).hexdigest()


class AgentMemoryStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    # ------------------------------------------------------------------
    # Session state
    # ------------------------------------------------------------------

    async def load_session_state(self, session_id: UUID) -> SessionMemoryState:
        async with self.pool.connection() as connection:
            result = await connection.execute(
                """SELECT public_id AS session_id, summary_text, summary_json,
                    summary_version, summary_token_count,
                    summary_covers_through_message_id,
                    extraction_covers_through_message_id
                FROM agent_sessions WHERE public_id = %s;""",
                (session_id,),
            )
            row = await result.fetchone()
        if row is None:
            raise ValueError(f"Agent session not found: {session_id}")
        return SessionMemoryState(
            session_id=row["session_id"],
            summary_text=row["summary_text"],
            summary_json=row["summary_json"],
            summary_version=row["summary_version"] or 0,
            summary_token_count=row["summary_token_count"] or 0,
            summary_covers_through_message_id=row[
                "summary_covers_through_message_id"
            ],
            extraction_covers_through_message_id=row[
                "extraction_covers_through_message_id"
            ],
        )

    async def list_sessions_with_meta(
        self, *, limit: int = 30
    ) -> list[dict[str, Any]]:
        async with self.pool.connection() as connection:
            result = await connection.execute(
                """SELECT public_id AS id, title, created_at, updated_at,
                    last_message_at
                FROM agent_sessions
                ORDER BY COALESCE(last_message_at, updated_at) DESC, id DESC
                LIMIT %s;""",
                (limit,),
            )
            return [dict(row) for row in await result.fetchall()]

    async def touch_last_message(self, session_id: UUID) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                "UPDATE agent_sessions SET last_message_at = now(), updated_at = now() "
                "WHERE public_id = %s;",
                (session_id,),
            )

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    async def load_messages_after(
        self,
        session_id: UUID,
        after_message_id: UUID | None,
        limit: int,
    ) -> list[MemoryMessage]:
        sql = """
            SELECT m.public_id AS id, s.public_id AS session_id, m.role, m.content,
                m.token_count, m.created_at
            FROM agent_messages m
            JOIN agent_sessions s ON s.id = m.session_id
            WHERE s.public_id = %s
              AND (%s::uuid IS NULL OR (m.created_at, m.id) > (
                    SELECT created_at, id FROM agent_messages
                    WHERE public_id = %s
                  ))
            ORDER BY m.created_at, m.id
            LIMIT %s;
        """
        async with self.pool.connection() as connection:
            result = await connection.execute(
                sql, (session_id, after_message_id, after_message_id, limit)
            )
            rows = await result.fetchall()
        return [
            MemoryMessage(
                id=row["id"],
                session_id=row["session_id"],
                role=row["role"],
                content=row["content"],
                token_count=row["token_count"] or 0,
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def unsummarized_token_count(
        self, session_id: UUID, after_message_id: UUID | None
    ) -> int:
        sql = """
            SELECT COALESCE(SUM(m.token_count), 0) AS total
            FROM agent_messages m
            JOIN agent_sessions s ON s.id = m.session_id
            WHERE s.public_id = %s
              AND (%s::uuid IS NULL OR (m.created_at, m.id) > (
                    SELECT created_at, id FROM agent_messages
                    WHERE public_id = %s
                  ));
        """
        async with self.pool.connection() as connection:
            result = await connection.execute(
                sql, (session_id, after_message_id, after_message_id)
            )
            row = await result.fetchone()
        return int(row["total"] or 0)

    # ------------------------------------------------------------------
    # Rolling summaries
    # ------------------------------------------------------------------

    async def save_summary(
        self, summary: ConversationSummary, expected_version: int
    ) -> bool:
        async with self.pool.connection() as connection, connection.transaction():
            updated = await connection.execute(
                """UPDATE agent_sessions
                SET summary_text = %s, summary_json = %s, summary_version = %s,
                    summary_token_count = %s,
                    summary_covers_through_message_id = %s, updated_at = now()
                WHERE public_id = %s AND summary_version = %s;""",
                (
                    summary.summary_text,
                    Jsonb(summary.summary_json),
                    summary.version,
                    summary.token_count,
                    summary.covers_through_message_id,
                    summary.session_id,
                    expected_version,
                ),
            )
            if (updated.rowcount or 0) != 1:
                return False
            await connection.execute(
                """INSERT INTO agent_conversation_summaries (
                    session_id, version, summary_text, summary_json, token_count,
                    covered_message_count, covers_through_message_id, provider, model
                )
                SELECT id, %s, %s, %s, %s, %s, %s, %s, %s
                FROM agent_sessions WHERE public_id = %s
                ON CONFLICT (session_id, version) DO NOTHING;""",
                (
                    summary.version,
                    summary.summary_text,
                    Jsonb(summary.summary_json),
                    summary.token_count,
                    summary.covered_message_count,
                    summary.covers_through_message_id,
                    summary.provider,
                    summary.model,
                    summary.session_id,
                ),
            )
        return True

    async def advance_extraction_watermark(
        self, session_id: UUID, through_message_id: UUID
    ) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """UPDATE agent_sessions
                SET extraction_covers_through_message_id = %s, updated_at = now()
                WHERE public_id = %s;""",
                (through_message_id, session_id),
            )

    # ------------------------------------------------------------------
    # Long-term memories
    # ------------------------------------------------------------------

    async def insert_memory(
        self,
        *,
        session_id: UUID | None,
        memory_type: str,
        content: str,
        confidence: float,
        source_message_id: UUID | None,
        expires_at: datetime | None,
    ) -> UUID | None:
        """Insert one memory; returns None when an identical active one exists."""

        content_hash = memory_content_hash(content)
        async with self.pool.connection() as connection:
            result = await connection.execute(
                """INSERT INTO agent_memories (
                    session_id, memory_type, content, content_hash, confidence,
                    source_message_id, expires_at
                )
                SELECT id, %s, %s, %s, %s, %s, %s
                FROM agent_sessions WHERE public_id = %s
                ON CONFLICT (content_hash) WHERE status = 'ACTIVE' DO NOTHING
                RETURNING public_id;""",
                (
                    memory_type,
                    content,
                    content_hash,
                    confidence,
                    source_message_id,
                    expires_at,
                    session_id,
                ),
            )
            row = await result.fetchone()
        return row["public_id"] if row else None

    async def supersede_memory(
        self, old_memory_id: UUID, new_memory_id: UUID
    ) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """UPDATE agent_memories
                SET status = %s, superseded_by = %s, updated_at = now()
                WHERE public_id = %s AND status = %s;""",
                (MEMORY_STATUS_SUPERSEDED, new_memory_id, old_memory_id, MEMORY_STATUS_ACTIVE),
            )

    async def load_active_memories(self, limit: int) -> list[MemoryRecord]:
        async with self.pool.connection() as connection:
            result = await connection.execute(
                """SELECT public_id AS id, session_id, memory_type, content,
                    content_hash, confidence::float8 AS confidence, status,
                    source_message_id, access_count, last_accessed_at, expires_at,
                    created_at, updated_at
                FROM agent_memories
                WHERE status = %s AND (expires_at IS NULL OR expires_at > now())
                ORDER BY updated_at DESC
                LIMIT %s;""",
                (MEMORY_STATUS_ACTIVE, limit),
            )
            rows = await result.fetchall()
        return [
            MemoryRecord(
                id=row["id"],
                session_id=row["session_id"],
                memory_type=row["memory_type"],
                content=row["content"],
                content_hash=row["content_hash"],
                confidence=row["confidence"],
                status=row["status"],
                source_message_id=row["source_message_id"],
                access_count=row["access_count"] or 0,
                last_accessed_at=row["last_accessed_at"],
                expires_at=row["expires_at"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    async def touch_memory_access(self, memory_ids: list[UUID]) -> None:
        if not memory_ids:
            return
        async with self.pool.connection() as connection:
            await connection.execute(
                """UPDATE agent_memories
                SET access_count = access_count + 1, last_accessed_at = now()
                WHERE public_id = ANY(%s);""",
                (memory_ids,),
            )

    async def count_active_memories(self) -> int:
        async with self.pool.connection() as connection:
            result = await connection.execute(
                "SELECT count(*) AS total FROM agent_memories "
                "WHERE status = %s;",
                (MEMORY_STATUS_ACTIVE,),
            )
            row = await result.fetchone()
        return int(row["total"])

    async def expire_lowest_value_memories(self, excess: int) -> int:
        if excess <= 0:
            return 0
        async with self.pool.connection() as connection:
            result = await connection.execute(
                """UPDATE agent_memories
                SET status = %s, updated_at = now()
                WHERE public_id IN (
                    SELECT public_id FROM agent_memories
                    WHERE status = %s
                    ORDER BY confidence ASC,
                        COALESCE(last_accessed_at, created_at) ASC
                    LIMIT %s
                );""",
                (MEMORY_STATUS_EXPIRED, MEMORY_STATUS_ACTIVE, excess),
            )
        return result.rowcount or 0

    # ------------------------------------------------------------------
    # Explicit preferences
    # ------------------------------------------------------------------

    async def load_user_preferences(self) -> dict[str, str]:
        async with self.pool.connection() as connection:
            result = await connection.execute(
                "SELECT preference_key, preference_value FROM agent_user_preferences;"
            )
            rows = await result.fetchall()
        return {row["preference_key"]: row["preference_value"] for row in rows}

    async def upsert_user_preference(self, key: str, value: str) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """INSERT INTO agent_user_preferences (preference_key, preference_value)
                VALUES (%s, %s)
                ON CONFLICT (preference_key)
                DO UPDATE SET preference_value = EXCLUDED.preference_value,
                    updated_at = now();""",
                (key, value),
            )

    async def delete_user_preference(self, key: str) -> bool:
        async with self.pool.connection() as connection:
            result = await connection.execute(
                "DELETE FROM agent_user_preferences WHERE preference_key = %s;",
                (key,),
            )
        return (result.rowcount or 0) > 0
