"""PostgreSQL persistence for AI Agent sessions, messages and approvals."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from wecanfindintern.agent.models import (
    AgentApproval,
    AgentMessage,
    AgentRole,
    AgentSession,
    AgentToolCall,
)


class AgentRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def create_session(self, *, title: str | None = None) -> AgentSession:
        async with self.pool.connection() as connection, connection.cursor(
            row_factory=dict_row
        ) as cursor:
            await cursor.execute(
                """INSERT INTO agent_sessions (title)
                VALUES (%s)
                RETURNING public_id AS id, title, created_at, updated_at;""",
                (title or "New conversation",),
            )
            return AgentSession.model_validate(await cursor.fetchone())

    async def list_sessions(self, *, limit: int = 20) -> list[AgentSession]:
        async with self.pool.connection() as connection:
            result = await connection.execute(
                """SELECT public_id AS id, title, created_at, updated_at
                FROM agent_sessions ORDER BY updated_at DESC, id DESC LIMIT %s;""",
                (limit,),
            )
            return [AgentSession.model_validate(row) for row in await result.fetchall()]

    async def get_session(self, public_id: UUID) -> AgentSession | None:
        async with self.pool.connection() as connection:
            result = await connection.execute(
                """SELECT public_id AS id, title, created_at, updated_at
                FROM agent_sessions WHERE public_id = %s;""",
                (public_id,),
            )
            row = await result.fetchone()
        return AgentSession.model_validate(row) if row else None

    async def update_session_title(
        self, public_id: UUID, title: str
    ) -> AgentSession | None:
        async with self.pool.connection() as connection:
            result = await connection.execute(
                """UPDATE agent_sessions SET title=%s, updated_at=now()
                WHERE public_id=%s
                RETURNING public_id AS id, title, created_at, updated_at;""",
                (title, public_id),
            )
            row = await result.fetchone()
        return AgentSession.model_validate(row) if row else None

    async def touch_session(self, public_id: UUID) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                "UPDATE agent_sessions SET updated_at=now() WHERE public_id=%s;",
                (public_id,),
            )

    async def add_message(
        self,
        session_id: UUID,
        role: AgentRole,
        content: str,
        *,
        token_count: int = 0,
    ) -> AgentMessage:
        async with self.pool.connection() as connection, connection.cursor(
            row_factory=dict_row
        ) as cursor:
            await cursor.execute(
                """INSERT INTO agent_messages (session_id, role, content, token_count)
                SELECT id, %s, %s, %s FROM agent_sessions WHERE public_id=%s
                RETURNING public_id AS id,
                    (SELECT public_id FROM agent_sessions WHERE id=agent_messages.session_id)
                        AS session_id,
                    role, content, created_at;""",
                (role, content, token_count, session_id),
            )
            row = await cursor.fetchone()
        return AgentMessage.model_validate(row)

    async def list_messages(
        self, session_id: UUID, *, limit: int = 60
    ) -> list[AgentMessage]:
        async with self.pool.connection() as connection:
            result = await connection.execute(
                """SELECT m.public_id AS id, s.public_id AS session_id, m.role, m.content,
                    m.created_at
                FROM agent_messages m
                JOIN agent_sessions s ON s.id = m.session_id
                WHERE s.public_id = %s
                ORDER BY m.created_at ASC, m.id ASC
                LIMIT %s;""",
                (session_id, limit),
            )
            return [AgentMessage.model_validate(row) for row in await result.fetchall()]

    async def add_tool_call(
        self,
        *,
        session_id: UUID,
        message_id: UUID | None,
        tool_name: str,
        arguments: dict[str, Any],
        status: str = "succeeded",
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> AgentToolCall:
        async with self.pool.connection() as connection, connection.cursor(
            row_factory=dict_row
        ) as cursor:
            await cursor.execute(
                """INSERT INTO agent_tool_calls (
                    session_id, message_id, tool_name, arguments, status, result, error
                )
                SELECT s.id, m.id, %s, %s, %s, %s, %s
                FROM agent_sessions s
                LEFT JOIN agent_messages m
                    ON m.public_id=%s AND m.session_id = s.id
                WHERE s.public_id=%s
                RETURNING public_id AS id,
                    (SELECT public_id FROM agent_sessions WHERE id=agent_tool_calls.session_id)
                        AS session_id,
                    (SELECT public_id FROM agent_messages WHERE id=agent_tool_calls.message_id)
                        AS message_id,
                    tool_name, arguments, status, result, error, created_at, updated_at;""",
                (
                    tool_name,
                    Jsonb(arguments),
                    status,
                    Jsonb(result) if result is not None else None,
                    error,
                    message_id,
                    session_id,
                ),
            )
            row = await cursor.fetchone()
        return AgentToolCall.model_validate(row)

    async def list_tool_calls(
        self, session_id: UUID, *, limit: int = 100
    ) -> list[AgentToolCall]:
        async with self.pool.connection() as connection:
            result = await connection.execute(
                """SELECT t.public_id AS id, s.public_id AS session_id,
                    m.public_id AS message_id, t.tool_name,
                    t.arguments, t.status, t.result, t.error, t.created_at, t.updated_at
                FROM agent_tool_calls t
                JOIN agent_sessions s ON s.id = t.session_id
                LEFT JOIN agent_messages m ON m.id = t.message_id
                WHERE s.public_id = %s
                ORDER BY t.created_at ASC, t.id ASC
                LIMIT %s;""",
                (session_id, limit),
            )
            return [AgentToolCall.model_validate(row) for row in await result.fetchall()]

    async def create_approval(
        self,
        *,
        session_id: UUID,
        tool_name: str,
        arguments: dict[str, Any],
        preview: dict[str, Any],
    ) -> AgentApproval:
        async with self.pool.connection() as connection, connection.cursor(
            row_factory=dict_row
        ) as cursor:
            await cursor.execute(
                """INSERT INTO agent_approvals (session_id, tool_name, arguments, preview)
                SELECT id, %s, %s, %s FROM agent_sessions WHERE public_id=%s
                RETURNING public_id AS id,
                    (SELECT public_id FROM agent_sessions WHERE id=agent_approvals.session_id)
                        AS session_id,
                    tool_name, arguments, preview, status, created_at, decided_at;""",
                (tool_name, Jsonb(arguments), Jsonb(preview), session_id),
            )
            row = await cursor.fetchone()
        return AgentApproval.model_validate(row)

    async def list_pending_approvals(self, session_id: UUID) -> list[AgentApproval]:
        async with self.pool.connection() as connection:
            result = await connection.execute(
                """SELECT a.public_id AS id, s.public_id AS session_id, a.tool_name,
                    a.arguments, a.preview, a.status, a.created_at, a.decided_at
                FROM agent_approvals a
                JOIN agent_sessions s ON s.id = a.session_id
                WHERE s.public_id = %s AND a.status = 'pending'
                ORDER BY a.created_at ASC, a.id ASC;""",
                (session_id,),
            )
            return [AgentApproval.model_validate(row) for row in await result.fetchall()]

    async def get_approval(self, public_id: UUID) -> AgentApproval | None:
        async with self.pool.connection() as connection:
            result = await connection.execute(
                """SELECT a.public_id AS id, s.public_id AS session_id, a.tool_name,
                    a.arguments, a.preview, a.status, a.created_at, a.decided_at
                FROM agent_approvals a
                JOIN agent_sessions s ON s.id = a.session_id
                WHERE a.public_id = %s;""",
                (public_id,),
            )
            row = await result.fetchone()
        return AgentApproval.model_validate(row) if row else None

    async def decide_approval(
        self, public_id: UUID, status: str
    ) -> AgentApproval | None:
        if status not in {"approved", "denied"}:
            raise ValueError(f"Invalid approval status: {status}")
        async with self.pool.connection() as connection:
            result = await connection.execute(
                """UPDATE agent_approvals SET status=%s, decided_at=now()
                WHERE public_id=%s AND status='pending'
                RETURNING public_id AS id,
                    (SELECT public_id FROM agent_sessions WHERE id=agent_approvals.session_id)
                        AS session_id,
                    tool_name, arguments, preview, status, created_at, decided_at;""",
                (status, public_id),
            )
            row = await result.fetchone()
        return AgentApproval.model_validate(row) if row else None

    async def append_audit(
        self,
        *,
        session_id: UUID | None = None,
        user_intent: str | None = None,
        tool_name: str | None = None,
        arguments_summary: str | None = None,
        approval_status: str | None = None,
        result_summary: str | None = None,
        error: str | None = None,
    ) -> None:
        async with self.pool.connection() as connection:
            if session_id is None:
                await connection.execute(
                    """INSERT INTO agent_audit_log (
                        session_id, user_intent, tool_name, arguments_summary,
                        approval_status, result_summary, error
                    ) VALUES (NULL, %s, %s, %s, %s, %s, %s);""",
                    (
                        user_intent,
                        tool_name,
                        arguments_summary,
                        approval_status,
                        result_summary,
                        error,
                    ),
                )
                return
            await connection.execute(
                """INSERT INTO agent_audit_log (
                    session_id, user_intent, tool_name, arguments_summary,
                    approval_status, result_summary, error
                )
                SELECT id, %s, %s, %s, %s, %s, %s
                FROM agent_sessions WHERE public_id = %s;""",
                (
                    user_intent,
                    tool_name,
                    arguments_summary,
                    approval_status,
                    result_summary,
                    error,
                    session_id,
                ),
            )
