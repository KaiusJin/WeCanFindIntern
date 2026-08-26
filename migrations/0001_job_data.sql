BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id           UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    provider            TEXT NOT NULL,
    sources             TEXT[] NOT NULL DEFAULT '{}',
    query               JSONB NOT NULL,
    status              SMALLINT NOT NULL DEFAULT 0,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    fetched_count       INTEGER NOT NULL DEFAULT 0,
    created_count       INTEGER NOT NULL DEFAULT 0,
    merged_count        INTEGER NOT NULL DEFAULT 0,
    unchanged_count     INTEGER NOT NULL DEFAULT 0,
    review_count        INTEGER NOT NULL DEFAULT 0,
    failed_count        INTEGER NOT NULL DEFAULT 0,
    error_summary       TEXT,
    CONSTRAINT ingestion_runs_status_chk CHECK (status BETWEEN 0 AND 3)
);

COMMENT ON COLUMN ingestion_runs.status IS '0=running, 1=succeeded, 2=partial, 3=failed';

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_started
    ON ingestion_runs (started_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS jobs (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id               UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    title                   TEXT NOT NULL,
    title_normalized        TEXT NOT NULL,
    company_name            TEXT,
    company_normalized      TEXT NOT NULL DEFAULT '',
    company_industry        TEXT,
    company_website_url     TEXT,
    company_logo_url        TEXT,
    location_text           TEXT,
    city                    TEXT,
    region_code             TEXT,
    country_code            CHAR(2),
    location_normalized     TEXT NOT NULL DEFAULT '',
    work_mode               TEXT NOT NULL DEFAULT 'unknown',
    employment_types        TEXT[] NOT NULL DEFAULT '{}',
    primary_employment_type TEXT,
    date_posted             DATE,
    published_sort_at       TIMESTAMPTZ NOT NULL,
    seniority               TEXT,
    job_function            TEXT,
    description             TEXT,
    description_hash        BYTEA,
    salary_interval         TEXT,
    salary_min              NUMERIC(14, 2),
    salary_max              NUMERIC(14, 2),
    salary_currency         CHAR(3),
    salary_source           TEXT,
    source_skills           TEXT[] NOT NULL DEFAULT '{}',
    contact_emails          TEXT[] NOT NULL DEFAULT '{}',
    experience_range        TEXT,
    vacancy_count           INTEGER,
    dedupe_block_key        BYTEA NOT NULL,
    status                  SMALLINT NOT NULL DEFAULT 1,
    source_count            INTEGER NOT NULL DEFAULT 0,
    first_seen_at           TIMESTAMPTZ NOT NULL,
    last_seen_at            TIMESTAMPTZ NOT NULL,
    last_verified_at        TIMESTAMPTZ NOT NULL,
    closed_at               TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    search_document TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('simple', coalesce(company_name, '')), 'A') ||
        setweight(to_tsvector('simple', coalesce(location_text, '')), 'B')
    ) STORED,
    CONSTRAINT jobs_status_chk CHECK (status BETWEEN 1 AND 4),
    CONSTRAINT jobs_work_mode_chk CHECK (work_mode IN ('onsite', 'hybrid', 'remote', 'unknown')),
    CONSTRAINT jobs_salary_order_chk CHECK (
        salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max
    ),
    CONSTRAINT jobs_source_count_chk CHECK (source_count >= 0),
    CONSTRAINT jobs_vacancy_count_chk CHECK (vacancy_count IS NULL OR vacancy_count >= 0)
);

COMMENT ON COLUMN jobs.status IS '1=active, 2=possibly_closed, 3=closed, 4=expired';
COMMENT ON COLUMN jobs.published_sort_at IS 'Non-null feed cursor: date_posted at UTC midnight, otherwise first_seen_at';
COMMENT ON COLUMN jobs.search_document IS 'Deliberately excludes description to keep the hot search index compact';

-- Feed and API filter indexes use the same keyset order as GET /jobs.
CREATE INDEX IF NOT EXISTS idx_jobs_active_feed
    ON jobs (published_sort_at DESC, id DESC)
    WHERE status = 1;

CREATE INDEX IF NOT EXISTS idx_jobs_active_country_region_feed
    ON jobs (country_code, region_code, published_sort_at DESC, id DESC)
    WHERE status = 1;

CREATE INDEX IF NOT EXISTS idx_jobs_active_company_feed
    ON jobs (company_normalized, published_sort_at DESC, id DESC)
    WHERE status = 1;

CREATE INDEX IF NOT EXISTS idx_jobs_active_work_mode_feed
    ON jobs (work_mode, published_sort_at DESC, id DESC)
    WHERE status = 1;

CREATE INDEX IF NOT EXISTS idx_jobs_active_employment_feed
    ON jobs (primary_employment_type, published_sort_at DESC, id DESC)
    WHERE status = 1;

CREATE INDEX IF NOT EXISTS idx_jobs_active_posted
    ON jobs (date_posted DESC, id DESC)
    WHERE status = 1 AND date_posted IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_jobs_active_salary
    ON jobs (salary_currency, salary_max, published_sort_at DESC, id DESC)
    WHERE status = 1 AND salary_max IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_jobs_active_search
    ON jobs USING GIN (search_document)
    WHERE status = 1;

-- Dedupe never performs a corpus-wide similarity scan. The block hash narrows
-- candidates before application-side scoring.
CREATE INDEX IF NOT EXISTS idx_jobs_active_dedupe_block
    ON jobs (dedupe_block_key, date_posted DESC, id DESC)
    WHERE status = 1;

CREATE INDEX IF NOT EXISTS idx_jobs_description_hash
    ON jobs (description_hash)
    WHERE description_hash IS NOT NULL;

CREATE TABLE IF NOT EXISTS job_sources (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id                  BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    source                  TEXT NOT NULL,
    source_job_id           TEXT,
    source_url              TEXT NOT NULL,
    canonical_source_url    TEXT NOT NULL,
    direct_url              TEXT,
    canonical_direct_url    TEXT,
    direct_url_hash         BYTEA,
    source_fingerprint      BYTEA NOT NULL,
    last_payload_hash       BYTEA NOT NULL,
    first_seen_at           TIMESTAMPTZ NOT NULL,
    last_seen_at            TIMESTAMPTZ NOT NULL,
    last_scraped_at         TIMESTAMPTZ NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT job_sources_fingerprint_len_chk CHECK (octet_length(source_fingerprint) = 32),
    CONSTRAINT job_sources_payload_hash_len_chk CHECK (octet_length(last_payload_hash) = 32),
    CONSTRAINT job_sources_direct_hash_len_chk CHECK (
        direct_url_hash IS NULL OR octet_length(direct_url_hash) = 32
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_job_sources_fingerprint
    ON job_sources (source_fingerprint);

CREATE UNIQUE INDEX IF NOT EXISTS uq_job_sources_source_job_id
    ON job_sources (source, source_job_id)
    WHERE source_job_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_job_sources_job
    ON job_sources (job_id, id);

CREATE INDEX IF NOT EXISTS idx_job_sources_source_job
    ON job_sources (source, job_id);

CREATE INDEX IF NOT EXISTS idx_job_sources_direct_hash
    ON job_sources (direct_url_hash, job_id)
    WHERE direct_url_hash IS NOT NULL;

CREATE TABLE IF NOT EXISTS raw_job_snapshots (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY,
    scraped_at          TIMESTAMPTZ NOT NULL,
    ingestion_run_id    BIGINT NOT NULL REFERENCES ingestion_runs(id) ON DELETE RESTRICT,
    job_source_id       BIGINT NOT NULL REFERENCES job_sources(id) ON DELETE CASCADE,
    source_fingerprint  BYTEA NOT NULL,
    payload_hash        BYTEA NOT NULL,
    payload             JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT raw_snapshots_fingerprint_len_chk CHECK (octet_length(source_fingerprint) = 32),
    CONSTRAINT raw_snapshots_payload_hash_len_chk CHECK (octet_length(payload_hash) = 32)
) PARTITION BY RANGE (scraped_at);

-- Safety net only. Normal ingestion creates the monthly partition before insert.
CREATE TABLE IF NOT EXISTS raw_job_snapshots_default
    PARTITION OF raw_job_snapshots DEFAULT;

CREATE INDEX IF NOT EXISTS idx_raw_snapshots_run
    ON raw_job_snapshots (ingestion_run_id, id);

CREATE INDEX IF NOT EXISTS idx_raw_snapshots_source_time
    ON raw_job_snapshots (job_source_id, scraped_at DESC);

CREATE INDEX IF NOT EXISTS idx_raw_snapshots_scraped_brin
    ON raw_job_snapshots USING BRIN (scraped_at) WITH (pages_per_range = 128);

CREATE OR REPLACE FUNCTION ensure_raw_job_snapshot_partition(target_time TIMESTAMPTZ)
RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
    partition_start TIMESTAMPTZ := date_trunc('month', target_time);
    partition_end   TIMESTAMPTZ := date_trunc('month', target_time) + interval '1 month';
    partition_name  TEXT := 'raw_job_snapshots_' || to_char(target_time, 'YYYYMM');
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext(partition_name));
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF raw_job_snapshots FOR VALUES FROM (%L) TO (%L)',
        partition_name,
        partition_start,
        partition_end
    );
END;
$$;

CREATE TABLE IF NOT EXISTS dedupe_candidates (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incoming_job_id     BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    candidate_job_id    BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    source_fingerprint  BYTEA NOT NULL,
    score               NUMERIC(5, 4) NOT NULL,
    evidence            JSONB NOT NULL,
    status              SMALLINT NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at         TIMESTAMPTZ,
    CONSTRAINT dedupe_candidates_distinct_jobs_chk CHECK (incoming_job_id <> candidate_job_id),
    CONSTRAINT dedupe_candidates_score_chk CHECK (score >= 0 AND score <= 1),
    CONSTRAINT dedupe_candidates_status_chk CHECK (status BETWEEN 0 AND 3)
);

COMMENT ON COLUMN dedupe_candidates.status IS '0=pending, 1=merged, 2=rejected, 3=expired';

CREATE UNIQUE INDEX IF NOT EXISTS uq_dedupe_candidates_pair
    ON dedupe_candidates (incoming_job_id, candidate_job_id);

CREATE INDEX IF NOT EXISTS idx_dedupe_candidates_pending
    ON dedupe_candidates (score DESC, id)
    WHERE status = 0;

CREATE TABLE IF NOT EXISTS job_status_history (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id          BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    from_status     SMALLINT,
    to_status       SMALLINT NOT NULL,
    reason          TEXT,
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ingestion_run_id BIGINT REFERENCES ingestion_runs(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_job_status_history_job_time
    ON job_status_history (job_id, changed_at DESC, id DESC);

COMMIT;
