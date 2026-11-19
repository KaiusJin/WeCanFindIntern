#!/usr/bin/env python3
"""Initialize an empty desktop database from database/schema.sql."""

import argparse
from pathlib import Path

from wecanfindintern.config import Settings
from wecanfindintern.desktop.schema import initialize_database


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", type=Path, default=Path("database/schema.sql"))
    args = parser.parse_args()
    created = initialize_database(Settings.from_env().database_url, args.schema)
    print("Database initialized." if created else "Database already initialized.")


if __name__ == "__main__":
    main()
