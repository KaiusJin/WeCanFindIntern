BEGIN;

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS recruiting_season TEXT,
    ADD COLUMN IF NOT EXISTS recruiting_year SMALLINT,
    ADD COLUMN IF NOT EXISTS recruiting_term_source TEXT,
    ADD COLUMN IF NOT EXISTS recruiting_term_evidence TEXT,
    ADD COLUMN IF NOT EXISTS recruiting_term_input_hash BYTEA,
    ADD COLUMN IF NOT EXISTS recruiting_term_checked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS recruiting_term_model TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'jobs_recruiting_season_chk'
    ) THEN
        ALTER TABLE jobs ADD CONSTRAINT jobs_recruiting_season_chk CHECK (
            recruiting_season IS NULL OR recruiting_season IN (
                'winter', 'spring', 'summer', 'fall'
            )
        );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'jobs_recruiting_year_chk'
    ) THEN
        ALTER TABLE jobs ADD CONSTRAINT jobs_recruiting_year_chk CHECK (
            recruiting_year IS NULL OR recruiting_year BETWEEN 2020 AND 2099
        );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_jobs_active_recruiting_term
    ON jobs (recruiting_year, recruiting_season, published_sort_at DESC, id DESC)
    WHERE status = 1 AND recruiting_season IS NOT NULL;

CREATE TABLE IF NOT EXISTS recruiting_term_generations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id              BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    input_hash          BYTEA NOT NULL,
    input_context       TEXT NOT NULL,
    model               TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    response_json       JSONB,
    prompt_tokens       INTEGER,
    completion_tokens   INTEGER,
    error_type          TEXT,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    CONSTRAINT recruiting_term_generations_status_chk CHECK (
        status IN ('pending', 'complete', 'error')
    )
);

CREATE INDEX IF NOT EXISTS idx_recruiting_term_generations_job
    ON recruiting_term_generations (job_id, started_at DESC);

COMMENT ON COLUMN jobs.recruiting_term_input_hash IS
    'SHA-256 of title and description last checked by regex/LLM term extraction';
COMMENT ON TABLE recruiting_term_generations IS
    'Persistent metadata and output for each DeepSeek recruiting-term generation';

COMMIT;
