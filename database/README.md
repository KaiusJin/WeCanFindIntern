# Database schema

This project is in development and stores only the final database definitions.
There is no numbered migration chain or migration tracking table. When the
schema changes, recreate development databases from these definitions.

## Desktop / local API

`database/schema.sql` creates the full desktop schema, indexes, constraints,
functions, triggers, and initial singleton rows. Requires PostgreSQL 16+ with
`pgcrypto`, `pg_trgm`, and `vector` available.

```bash
PYTHONPATH=src .venv/bin/python scripts/maintenance/init_db.py
```

The desktop bundles this file and initializes an empty database on first launch.
Subsequent launches skip initialization when `public.jobs` already exists; this
is not an upgrade mechanism. To apply SQL manually to an empty database:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 --single-transaction -f database/schema.sql
```

## Cloud job feed

`scripts/cloud/bootstrap_job_feed.sql` is a separate, self-contained schema for
the Supabase collector. Run the entire file once as `postgres` in a fresh jobs
database after creating the `job_collector` login and setting its password.
It creates only cloud collection tables in the private `job_feed` schema.
Desktop Profile, Tracker, Agent, and recommendation tables stay local.

The collector does not create or upgrade its schema on startup. See
[Cloud job feed](../docs/CLOUD_JOB_FEED.md) for credentials and workflow setup.

Raw snapshots use a default partition; the existing partition helper creates
monthly partitions as snapshots arrive.
