"""Initialize a fresh desktop database from the final schema."""

from pathlib import Path

import psycopg


def initialize_database(database_url: str, schema_path: Path) -> bool:
    """Create the schema on first launch; leave initialized databases alone."""

    with psycopg.connect(database_url) as connection:
        if connection.execute("SELECT to_regclass('public.jobs')").fetchone()[0]:
            return False
        connection.execute(schema_path.read_text(encoding="utf-8"), prepare=False)
    return True
