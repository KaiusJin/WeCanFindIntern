#!/usr/bin/env python3
"""Apply SQL migrations without requiring a local psql binary."""

from __future__ import annotations

import argparse
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
    args = parser.parse_args()

    migration_files = sorted(args.migration_dir.glob("*.sql"))
    if not migration_files:
        parser.error(f"没有找到迁移文件: {args.migration_dir}")

    settings = Settings.from_env()
    with psycopg.connect(settings.database_url, autocommit=True) as connection:
        for path in migration_files:
            print(f"执行迁移: {path}")
            connection.execute(path.read_text(encoding="utf-8"), prepare=False)
    print(f"迁移完成: {len(migration_files)} 个文件")


if __name__ == "__main__":
    main()
