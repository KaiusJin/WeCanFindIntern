"""Apply packaged PostgreSQL migrations before the desktop API starts."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import psycopg

TRANSACTION_START = re.compile(r"\A\s*BEGIN;\s*", re.IGNORECASE)
TRANSACTION_END = re.compile(r"\s*COMMIT;\s*\Z", re.IGNORECASE)


def migration_body(contents: str, *, name: str) -> str:
    """Remove a migration's outer transaction so tracking is atomic with it."""

    has_start = TRANSACTION_START.search(contents) is not None
    has_end = TRANSACTION_END.search(contents) is not None
    if has_start != has_end:
        raise RuntimeError(f"Migration has an incomplete transaction wrapper: {name}")
    if not has_start:
        return contents
    without_start = TRANSACTION_START.sub("", contents, count=1)
    return TRANSACTION_END.sub("", without_start, count=1)


def apply_migrations(database_url: str, migration_dir: Path) -> list[str]:
    migration_files = sorted(migration_dir.glob("*.sql"))
    if not migration_files:
        raise RuntimeError(f"No packaged migrations found in {migration_dir}")

    applied_now: list[str] = []
    # Autocommit is used between migrations. Each migration and its checksum
    # record are committed together inside the explicit transaction below.
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                checksum CHAR(64) NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );"""
        )
        applied = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT version,checksum FROM schema_migrations;"
            ).fetchall()
        }
        for path in migration_files:
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            if path.name in applied:
                if applied[path.name] != checksum:
                    raise RuntimeError(f"Applied migration checksum changed: {path.name}")
                continue
            contents = migration_body(path.read_text(encoding="utf-8"), name=path.name)
            with connection.transaction():
                connection.execute(contents, prepare=False)
                connection.execute(
                    "INSERT INTO schema_migrations (version,checksum) VALUES (%s,%s);",
                    (path.name, checksum),
                )
            applied_now.append(path.name)
    return applied_now
