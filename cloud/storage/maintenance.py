"""Campaign lock and retention cleanup for the private job_feed schema."""

CAMPAIGN_LOCK_ID = 790341567


def acquire_campaign_lock(db):
    """Fail unless connected to job_feed and no other campaign holds the lock."""
    schema, locked = db.execute(
        f"SELECT current_schema(), pg_try_advisory_lock({CAMPAIGN_LOCK_ID})"
    ).fetchone()
    if schema != "job_feed" or not locked:
        raise RuntimeError("Expected the private job_feed schema and an exclusive campaign lock")
    return db.execute("SELECT now()").fetchone()[0]


def maintain(db, *, expire):
    with db.transaction():
        if expire:
            db.execute("""
                UPDATE jobs SET status=4, closed_at=now()
                WHERE status IN (1,2) AND last_seen_at < now()-interval '14 days'
            """)
        db.execute("""
            DELETE FROM jobs WHERE status IN (3,4) AND closed_at < now()-interval '7 days'
        """)
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
