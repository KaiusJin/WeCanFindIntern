BEGIN;

-- These arrays contain normalized requirement tags such as education and
-- experience, not a second required/preferred skill taxonomy.
ALTER TABLE recommendation_documents
    RENAME COLUMN required_skills TO requirement_tags;
ALTER TABLE recommendation_documents
    DROP COLUMN preferred_skills;

-- A schema/document-version migration must not wait for each job to be edited.
-- Requeue the active corpus so the maintenance loop rebuilds it in place.
INSERT INTO recommendation_index_queue(public_job_id, queued_at, attempts, last_error)
SELECT public_id, now(), 0, NULL
FROM jobs
WHERE status=1
ON CONFLICT(public_job_id) DO UPDATE SET
    queued_at=EXCLUDED.queued_at,
    attempts=0,
    last_error=NULL;

COMMIT;
