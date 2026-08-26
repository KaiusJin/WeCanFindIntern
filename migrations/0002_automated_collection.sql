BEGIN;

CREATE TABLE IF NOT EXISTS dedupe_decisions (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_fingerprint  BYTEA NOT NULL,
    resulting_job_id    BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    compared_job_id     BIGINT REFERENCES jobs(id) ON DELETE SET NULL,
    action              TEXT NOT NULL,
    score               NUMERIC(5, 4) NOT NULL,
    algorithm_version   SMALLINT NOT NULL,
    evidence            JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT dedupe_decisions_action_chk CHECK (action IN ('merge', 'create')),
    CONSTRAINT dedupe_decisions_score_chk CHECK (score >= 0 AND score <= 1),
    CONSTRAINT dedupe_decisions_fingerprint_len_chk
        CHECK (octet_length(source_fingerprint) = 32)
);

CREATE INDEX IF NOT EXISTS idx_dedupe_decisions_source_time
    ON dedupe_decisions (source_fingerprint, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_dedupe_decisions_result_time
    ON dedupe_decisions (resulting_job_id, created_at DESC);

-- Legacy uncertain candidates are automatically treated as separate listings.
UPDATE dedupe_candidates
SET status = 3,
    reviewed_at = coalesce(reviewed_at, now())
WHERE status = 0;

CREATE TABLE IF NOT EXISTS collection_plans (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id               UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    name                    TEXT NOT NULL UNIQUE,
    enabled                 BOOLEAN NOT NULL DEFAULT true,
    sites                   TEXT[] NOT NULL,
    query                   JSONB NOT NULL,
    interval_seconds        INTEGER NOT NULL DEFAULT 14400,
    page_size               SMALLINT NOT NULL DEFAULT 25,
    max_results_per_source  INTEGER NOT NULL DEFAULT 200,
    max_attempts            SMALLINT NOT NULL DEFAULT 5,
    next_run_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    active_run_id           BIGINT REFERENCES ingestion_runs(id) ON DELETE SET NULL,
    lease_owner             TEXT,
    lease_expires_at        TIMESTAMPTZ,
    last_started_at         TIMESTAMPTZ,
    last_completed_at       TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT collection_plans_sites_chk CHECK (cardinality(sites) > 0),
    CONSTRAINT collection_plans_interval_chk CHECK (interval_seconds >= 300),
    CONSTRAINT collection_plans_page_size_chk CHECK (page_size BETWEEN 1 AND 100),
    CONSTRAINT collection_plans_result_limit_chk CHECK (max_results_per_source BETWEEN 1 AND 1000),
    CONSTRAINT collection_plans_attempts_chk CHECK (max_attempts BETWEEN 1 AND 10)
);

CREATE INDEX IF NOT EXISTS idx_collection_plans_due
    ON collection_plans (next_run_at, id)
    WHERE enabled = true;

CREATE INDEX IF NOT EXISTS idx_collection_plans_expired_lease
    ON collection_plans (lease_expires_at, id)
    WHERE lease_owner IS NOT NULL;

CREATE TABLE IF NOT EXISTS collection_checkpoints (
    plan_id             BIGINT NOT NULL REFERENCES collection_plans(id) ON DELETE CASCADE,
    source              TEXT NOT NULL,
    run_id              BIGINT REFERENCES ingestion_runs(id) ON DELETE SET NULL,
    offset_value        INTEGER NOT NULL DEFAULT 0,
    status              SMALLINT NOT NULL DEFAULT 0,
    attempts            SMALLINT NOT NULL DEFAULT 0,
    pages_completed     INTEGER NOT NULL DEFAULT 0,
    records_seen        INTEGER NOT NULL DEFAULT 0,
    next_retry_at       TIMESTAMPTZ,
    last_error          TEXT,
    last_page_size      SMALLINT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (plan_id, source),
    CONSTRAINT collection_checkpoints_offset_chk CHECK (offset_value >= 0),
    CONSTRAINT collection_checkpoints_status_chk CHECK (status BETWEEN 0 AND 4),
    CONSTRAINT collection_checkpoints_attempts_chk CHECK (attempts BETWEEN 0 AND 10)
);

COMMENT ON COLUMN collection_checkpoints.status IS
    '0=idle, 1=running, 2=retry_wait, 3=succeeded, 4=exhausted';

CREATE INDEX IF NOT EXISTS idx_collection_checkpoints_retry
    ON collection_checkpoints (next_retry_at, plan_id)
    WHERE status = 2;

ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS collection_plan_id BIGINT
        REFERENCES collection_plans(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_plan_started
    ON ingestion_runs (collection_plan_id, started_at DESC, id DESC)
    WHERE collection_plan_id IS NOT NULL;

COMMIT;

