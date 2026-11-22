"""Campaign lock, the four-month archive and retention cleanup for the job_feed schema.

Retention rule: a job stays live for **four months after it was first written
to the database** (``jobs.created_at``), whether or not sources still list it.
After that it is moved to ``jobs_archive`` (job row + source rows as JSON) and
deleted from ``jobs``. The public feed only exports ``jobs``, so the next
publish sends a tombstone and desktop installs stop carrying the job.
"""

ARCHIVE_AFTER = "4 months"
CAMPAIGN_LOCK_ID = 790341567

# Created on first use: the collector role owns job_feed (CREATE is checked by
# the workflow's database validation), and the statement is idempotent.
ARCHIVE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS jobs_archive (
    id bigint PRIMARY KEY,
    public_id uuid NOT NULL,
    title text NOT NULL,
    company_name text,
    location_text text,
    country_code character(2),
    opportunity_type text,
    job_category text,
    created_at timestamp with time zone NOT NULL,
    first_seen_at timestamp with time zone,
    last_seen_at timestamp with time zone,
    archived_at timestamp with time zone NOT NULL DEFAULT now(),
    job jsonb NOT NULL,
    sources jsonb NOT NULL DEFAULT '[]'::jsonb
);
CREATE INDEX IF NOT EXISTS jobs_archive_archived_at_idx ON jobs_archive (archived_at);
CREATE INDEX IF NOT EXISTS jobs_archive_public_id_idx ON jobs_archive (public_id);
"""

ARCHIVE_SQL = f"""
WITH moved AS (
    INSERT INTO jobs_archive (
        id, public_id, title, company_name, location_text, country_code,
        opportunity_type, job_category, created_at, first_seen_at, last_seen_at,
        archived_at, job, sources
    )
    SELECT j.id, j.public_id, j.title, j.company_name, j.location_text, j.country_code,
           j.opportunity_type, j.job_category, j.created_at, j.first_seen_at, j.last_seen_at,
           now(),
           to_jsonb(j) - 'search_document',
           coalesce(
               (SELECT jsonb_agg(to_jsonb(s) ORDER BY s.id) FROM job_sources s
                WHERE s.job_id = j.id),
               '[]'::jsonb
           )
    FROM jobs j
    WHERE j.created_at < now() - interval '{ARCHIVE_AFTER}'
    ON CONFLICT (id) DO UPDATE SET
        job = EXCLUDED.job, sources = EXCLUDED.sources, archived_at = EXCLUDED.archived_at
    RETURNING id
)
DELETE FROM jobs WHERE id IN (SELECT id FROM moved)
"""


def acquire_campaign_lock(db):
    """Fail unless connected to job_feed and no other campaign holds the lock."""
    schema, locked = db.execute(
        f"SELECT current_schema(), pg_try_advisory_lock({CAMPAIGN_LOCK_ID})"
    ).fetchone()
    if schema != "job_feed" or not locked:
        raise RuntimeError("Expected the private job_feed schema and an exclusive campaign lock")
    return db.execute("SELECT now()").fetchone()[0]


def maintain(db) -> int:
    """Archive jobs first written over four months ago; prune short-lived history.

    Returns the number of jobs archived. Runs after every campaign, complete or
    partial: the rule depends only on each job's age, not on what this run saw.
    """
    with db.transaction():
        db.execute(ARCHIVE_TABLE_SQL)
        archived = db.execute(ARCHIVE_SQL).rowcount or 0
        # Keep a current LinkedIn detail payload for the existing read-through cache.
        db.execute("""
            DELETE FROM raw_job_snapshots r
            WHERE r.scraped_at < now()-interval '1 day'
              AND NOT EXISTS (
                SELECT FROM job_sources s WHERE s.id=r.job_source_id AND s.source='linkedin'
                  AND r.id=(SELECT id FROM raw_job_snapshots WHERE job_source_id=s.id
                            ORDER BY scraped_at DESC,id DESC LIMIT 1)
              )
        """)
        db.execute("DELETE FROM dedupe_decisions WHERE created_at < now()-interval '7 days'")
        # Successful sweep count is the existing collector's durable full-scan clock.
        # Keep those lightweight counters, and clear obsolete diagnostic text.
        db.execute("""
            UPDATE ingestion_runs SET error_summary=NULL
            WHERE error_summary IS NOT NULL AND started_at < now()-interval '7 days'
        """)
    print(f"Archived {archived} jobs first written more than {ARCHIVE_AFTER} ago", flush=True)
    return archived
