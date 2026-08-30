#!/usr/bin/env python3
"""Apply SQL migrations without requiring a local psql binary."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import psycopg

from wecanfindintern.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--migration-dir",
        type=Path,
        default=Path("migrations"),
    )
    parser.add_argument(
        "--baseline-existing",
        action="store_true",
        help="Record existing migrations without executing them (legacy databases only).",
    )
    args = parser.parse_args()

    migration_files = sorted(args.migration_dir.glob("*.sql"))
    if not migration_files:
        parser.error(f"没有找到迁移文件: {args.migration_dir}")

    settings = Settings.from_env()
    with psycopg.connect(settings.database_url) as connection:
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
        if args.baseline_existing:
            for path in migration_files:
                checksum = hashlib.sha256(path.read_bytes()).hexdigest()
                connection.execute(
                    """INSERT INTO schema_migrations (version,checksum)
                    VALUES (%s,%s) ON CONFLICT (version) DO NOTHING;""",
                    (path.name, checksum),
                )
            connection.commit()
            print(f"已记录现有迁移基线: {len(migration_files)} 个文件")
            return
        for path in migration_files:
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            if path.name in applied:
                if applied[path.name] != checksum:
                    raise RuntimeError(f"已应用迁移内容发生变化: {path}")
                print(f"跳过已应用迁移: {path}")
                continue
            print(f"执行迁移: {path}")
            connection.execute(path.read_text(encoding="utf-8"), prepare=False)
            connection.execute(
                "INSERT INTO schema_migrations (version,checksum) VALUES (%s,%s);",
                (path.name, checksum),
            )
            connection.commit()
    print(f"迁移完成: {len(migration_files)} 个文件")


if __name__ == "__main__":
    main()
