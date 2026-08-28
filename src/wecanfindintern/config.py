"""Environment configuration with intentionally small surface area."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    db_pool_min_size: int = 2
    db_pool_max_size: int = 20
    db_statement_timeout_ms: int = 5_000

    @classmethod
    def from_env(cls) -> Settings:
        database_url = os.getenv("DATABASE_URL")
        if not database_url and os.path.exists(".env"):
            with open(".env", "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip().strip("'\""))
            database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL is required")
        minimum = int(os.getenv("DB_POOL_MIN_SIZE", "2"))
        maximum = int(os.getenv("DB_POOL_MAX_SIZE", "20"))
        if minimum < 0 or maximum < 1 or minimum > maximum:
            raise RuntimeError("Invalid DB_POOL_MIN_SIZE/DB_POOL_MAX_SIZE")
        return cls(
            database_url=database_url,
            db_pool_min_size=minimum,
            db_pool_max_size=maximum,
            db_statement_timeout_ms=int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "5000")),
        )
