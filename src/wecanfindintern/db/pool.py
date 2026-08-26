"""Bounded async PostgreSQL connection pool."""

from __future__ import annotations

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from wecanfindintern.config import Settings


class Database:
    def __init__(self, settings: Settings) -> None:
        self.pool = AsyncConnectionPool(
            conninfo=settings.database_url,
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
            open=False,
            timeout=10,
            kwargs={
                "row_factory": dict_row,
                "prepare_threshold": 5,
                "options": f"-c statement_timeout={settings.db_statement_timeout_ms}",
            },
        )

    async def open(self) -> None:
        await self.pool.open(wait=True, timeout=15)

    async def close(self) -> None:
        await self.pool.close()
