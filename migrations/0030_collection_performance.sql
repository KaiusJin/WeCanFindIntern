BEGIN;

ALTER TABLE job_sources
    ADD COLUMN IF NOT EXISTS details_fetched_at TIMESTAMPTZ;

-- Existing LinkedIn snapshots already contain the detail payload. Seed the
-- freshness clock so the first post-upgrade campaign does not redownload every JD.
UPDATE job_sources js
SET details_fetched_at = js.last_scraped_at
WHERE js.source = 'linkedin'
  AND js.details_fetched_at IS NULL
  AND EXISTS (
      SELECT 1
      FROM raw_job_snapshots snapshot
      WHERE snapshot.job_source_id = js.id
        AND nullif(snapshot.payload->>'description', '') IS NOT NULL
  );

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS salary_enrichment_input_hash BYTEA,
    ADD COLUMN IF NOT EXISTS salary_enrichment_status TEXT,
    ADD COLUMN IF NOT EXISTS salary_enrichment_checked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS salary_enrichment_model TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'jobs_salary_enrichment_status_chk'
    ) THEN
        ALTER TABLE jobs ADD CONSTRAINT jobs_salary_enrichment_status_chk CHECK (
            salary_enrichment_status IS NULL OR
            salary_enrichment_status IN ('complete', 'not_found', 'error')
        );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_job_sources_linkedin_detail_freshness
    ON job_sources (details_fetched_at DESC)
    WHERE source = 'linkedin';

COMMENT ON COLUMN job_sources.details_fetched_at IS
    'Last successful provider detail-page fetch; list-card observations do not advance it';
COMMENT ON COLUMN jobs.salary_enrichment_input_hash IS
    'Description hash last checked by deterministic/LLM salary enrichment';

COMMIT;
