-- Supabase SQL Editor: run this entire file as postgres in the public jobs project.
-- This bootstrap matches the cloud migration set at commit 887e776. Later releases
-- apply their additional migrations through the Public job feed workflow.
-- Prerequisite: a job_collector login with a password for GitHub Actions.

BEGIN;
SELECT pg_advisory_xact_lock(790341567);

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA extensions;
-- Use the existing workflow login. Its password is managed separately.
GRANT job_collector TO postgres;
CREATE SCHEMA IF NOT EXISTS job_feed AUTHORIZATION job_collector;
REVOKE ALL ON SCHEMA job_feed FROM PUBLIC, anon, authenticated;
GRANT USAGE, CREATE ON SCHEMA job_feed TO job_collector;
GRANT USAGE ON SCHEMA extensions TO job_collector;
GRANT TEMPORARY ON DATABASE postgres TO job_collector;
ALTER ROLE job_collector SET search_path = job_feed, extensions;

-- An earlier bootstrap created feed objects as wcfi_collector. Transfer only that
-- role's feed objects so the new login can maintain tables and monthly partitions.
-- Indexes and table-owned sequences follow their tables automatically.
DO $adopt$
DECLARE
    ddl text;
BEGIN
    FOR ddl IN
        SELECT format('ALTER %s %s OWNER TO job_collector',
                      CASE WHEN relkind = 'S' THEN 'SEQUENCE' ELSE 'TABLE' END, oid::regclass)
        FROM pg_class
        WHERE relnamespace = 'job_feed'::regnamespace AND relowner = to_regrole('wcfi_collector')
          AND (relkind IN ('r','p') OR (relkind = 'S' AND relname = 'feed_release_seq'))
        UNION ALL
        SELECT format('ALTER FUNCTION %s OWNER TO job_collector', oid::regprocedure)
        FROM pg_proc
        WHERE pronamespace = 'job_feed'::regnamespace AND proowner = to_regrole('wcfi_collector')
          AND prokind = 'f'
    LOOP
        EXECUTE ddl;
    END LOOP;
END
$adopt$;

SET LOCAL ROLE job_collector;
SET LOCAL search_path = job_feed, extensions;
CREATE TABLE IF NOT EXISTS job_feed.schema_migrations (
    version text PRIMARY KEY,
    checksum char(64) NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);

-- Use the same filenames and SHA-256 values as the Python migration runner.
CREATE OR REPLACE FUNCTION pg_temp.apply_job_feed_file(filename text, file_hash text, ddl text)
RETURNS void LANGUAGE plpgsql AS $apply$
DECLARE
    recorded text;
BEGIN
    SELECT checksum INTO recorded FROM job_feed.schema_migrations WHERE version = filename;
    IF NOT FOUND THEN
        EXECUTE ddl;
        INSERT INTO job_feed.schema_migrations(version, checksum) VALUES (filename, file_hash);
    ELSIF recorded <> file_hash THEN
        RAISE EXCEPTION 'Applied migration checksum changed: %', filename;
    END IF;
END
$apply$;

SELECT pg_temp.apply_job_feed_file(
    '0001_job_data.sql',
    '4a397928e91fde6e9f699c2ac0fa417520bc7711d9bf3879f343a2613503bf47',
    $ddl$
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
$ddl$);

SELECT pg_temp.apply_job_feed_file(
    '0002_automated_collection.sql',
    'ca8fada0ce6450e67d2db8e38ac52ed7569ca688da49855c8e01cb28e9c49a13',
    $ddl$
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
$ddl$);

SELECT pg_temp.apply_job_feed_file(
    '0003_job_classification.sql',
    '49cbc019ad601bca84130825dbd3fea6bf43989f8f448943b7938985e2303188',
    $ddl$
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS opportunity_type TEXT NOT NULL DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS schedule_types TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS primary_schedule_type TEXT NOT NULL DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS job_category TEXT NOT NULL DEFAULT 'other',
    ADD COLUMN IF NOT EXISTS job_subcategories TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS seniority_level TEXT NOT NULL DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS education_levels TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS experience_min_years SMALLINT,
    ADD COLUMN IF NOT EXISTS experience_max_years SMALLINT,
    ADD COLUMN IF NOT EXISTS skill_tags TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS requirement_tags TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS display_tags TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS classification_version SMALLINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS salary_annual_min NUMERIC(14, 2),
    ADD COLUMN IF NOT EXISTS salary_annual_max NUMERIC(14, 2);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'jobs_opportunity_type_chk'
    ) THEN
        ALTER TABLE jobs ADD CONSTRAINT jobs_opportunity_type_chk CHECK (
            opportunity_type IN (
                'internship', 'co_op', 'new_grad', 'apprenticeship', 'regular',
                'contract', 'temporary', 'seasonal', 'unknown'
            )
        );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'jobs_primary_schedule_type_chk'
    ) THEN
        ALTER TABLE jobs ADD CONSTRAINT jobs_primary_schedule_type_chk CHECK (
            primary_schedule_type IN ('full_time', 'part_time', 'flexible', 'unknown')
        );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'jobs_job_category_chk'
    ) THEN
        ALTER TABLE jobs ADD CONSTRAINT jobs_job_category_chk CHECK (
            job_category IN (
                'software_engineering', 'data_ai', 'cybersecurity', 'cloud_devops',
                'qa_testing', 'product_design', 'product_management', 'it_support',
                'hardware_embedded', 'research', 'business_operations', 'finance',
                'marketing_sales', 'human_resources', 'healthcare', 'education',
                'skilled_trades', 'engineering', 'architecture_planning', 'legal',
                'customer_service', 'supply_chain', 'administrative', 'other'
            )
        );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'jobs_seniority_level_chk'
    ) THEN
        ALTER TABLE jobs ADD CONSTRAINT jobs_seniority_level_chk CHECK (
            seniority_level IN (
                'student', 'entry_level', 'junior', 'mid_level', 'senior',
                'lead', 'manager', 'director', 'executive', 'unknown'
            )
        );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'jobs_experience_years_chk'
    ) THEN
        ALTER TABLE jobs ADD CONSTRAINT jobs_experience_years_chk CHECK (
            (experience_min_years IS NULL OR experience_min_years BETWEEN 0 AND 30)
            AND (experience_max_years IS NULL OR experience_max_years BETWEEN 0 AND 30)
            AND (
                experience_min_years IS NULL OR experience_max_years IS NULL
                OR experience_min_years <= experience_max_years
            )
        );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'jobs_annual_salary_order_chk'
    ) THEN
        ALTER TABLE jobs ADD CONSTRAINT jobs_annual_salary_order_chk CHECK (
            salary_annual_min IS NULL OR salary_annual_max IS NULL
            OR salary_annual_min <= salary_annual_max
        );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_jobs_active_opportunity_feed
    ON jobs (opportunity_type, published_sort_at DESC, id DESC)
    WHERE status = 1;

CREATE INDEX IF NOT EXISTS idx_jobs_active_schedule_feed
    ON jobs (primary_schedule_type, published_sort_at DESC, id DESC)
    WHERE status = 1;

CREATE INDEX IF NOT EXISTS idx_jobs_active_category_feed
    ON jobs (job_category, published_sort_at DESC, id DESC)
    WHERE status = 1;

CREATE INDEX IF NOT EXISTS idx_jobs_active_seniority_feed
    ON jobs (seniority_level, published_sort_at DESC, id DESC)
    WHERE status = 1;

CREATE INDEX IF NOT EXISTS idx_jobs_active_skill_tags
    ON jobs USING GIN (skill_tags)
    WHERE status = 1;

CREATE INDEX IF NOT EXISTS idx_jobs_active_subcategories
    ON jobs USING GIN (job_subcategories)
    WHERE status = 1;

CREATE INDEX IF NOT EXISTS idx_jobs_active_annual_salary
    ON jobs (salary_currency, salary_annual_max, published_sort_at DESC, id DESC)
    WHERE status = 1 AND salary_annual_max IS NOT NULL;

COMMENT ON COLUMN jobs.opportunity_type IS
    'Internship/co-op/new-grad/regular/contract and related opportunity classification';
COMMENT ON COLUMN jobs.primary_schedule_type IS
    'Normalized full-time/part-time/flexible schedule, separate from opportunity type';
COMMENT ON COLUMN jobs.classification_version IS
    'Version of deterministic classification rules used for derived fields';
COMMENT ON COLUMN jobs.salary_annual_min IS
    'Comparable annualized salary using 2080 hours, 260 days, 52 weeks, or 12 months';
$ddl$);

SELECT pg_temp.apply_job_feed_file(
    '0004_expand_job_categories.sql',
    '31db2b3680fa43a918d642de0c9b569435ae434de6f29a290d81a0c62c35403f',
    $ddl$
ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_job_category_chk;

ALTER TABLE jobs ADD CONSTRAINT jobs_job_category_chk CHECK (
    job_category IN (
        'software_engineering', 'data_ai', 'cybersecurity', 'cloud_devops',
        'qa_testing', 'product_design', 'product_management', 'it_support',
        'hardware_embedded', 'research', 'business_operations', 'finance',
        'marketing_sales', 'human_resources', 'healthcare', 'education',
        'skilled_trades', 'engineering', 'architecture_planning', 'legal',
        'customer_service', 'supply_chain', 'administrative', 'other'
    )
);
$ddl$);

SELECT pg_temp.apply_job_feed_file(
    '0005_location_hierarchy.sql',
    '1dc4360209954ef4d0c22674c862d9b477b970a012ac0ea352708dda6c5a9740',
    $ddl$
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS region_name TEXT,
    ADD COLUMN IF NOT EXISTS region_type TEXT,
    ADD COLUMN IF NOT EXISTS country_name TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'jobs_region_type_chk'
    ) THEN
        ALTER TABLE jobs ADD CONSTRAINT jobs_region_type_chk CHECK (
            region_type IS NULL OR region_type IN ('province', 'territory', 'state', 'region')
        );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_jobs_active_location_hierarchy_feed
    ON jobs (country_code, region_code, city, published_sort_at DESC, id DESC)
    WHERE status = 1;

COMMENT ON COLUMN jobs.region_name IS 'Display name for state, province, territory, or region';
COMMENT ON COLUMN jobs.region_type IS 'province, territory, state, or generic region';
COMMENT ON COLUMN jobs.country_name IS 'Display country name paired with ISO country_code';
$ddl$);

SELECT pg_temp.apply_job_feed_file(
    '0007_recruiting_term.sql',
    '0b97eb42906971874bbdaaf48fc32bc76909617e7b116dd4569c234849f96bba',
    $ddl$
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
$ddl$);

SELECT pg_temp.apply_job_feed_file(
    '0008_salary_interval_integrity.sql',
    '60af467c860158bc4044c0685f0d95c40e9460c4107b27d8cd80243772091905',
    $ddl$
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'jobs_salary_interval_amount_chk'
    ) THEN
        ALTER TABLE jobs ADD CONSTRAINT jobs_salary_interval_amount_chk CHECK (
            salary_interval IS NULL
            OR (salary_interval = 'hourly'
                AND (salary_min IS NULL OR salary_min BETWEEN 5 AND 500)
                AND (salary_max IS NULL OR salary_max BETWEEN 5 AND 500))
            OR (salary_interval = 'daily'
                AND (salary_min IS NULL OR salary_min BETWEEN 40 AND 5000)
                AND (salary_max IS NULL OR salary_max BETWEEN 40 AND 5000))
            OR (salary_interval = 'weekly'
                AND (salary_min IS NULL OR salary_min BETWEEN 100 AND 25000)
                AND (salary_max IS NULL OR salary_max BETWEEN 100 AND 25000))
            OR (salary_interval = 'monthly'
                AND (salary_min IS NULL OR salary_min BETWEEN 500 AND 100000)
                AND (salary_max IS NULL OR salary_max BETWEEN 500 AND 100000))
            OR (salary_interval = 'yearly'
                AND (salary_min IS NULL OR salary_min BETWEEN 5000 AND 2000000)
                AND (salary_max IS NULL OR salary_max BETWEEN 5000 AND 2000000))
        );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'jobs_salary_annual_amount_chk'
    ) THEN
        ALTER TABLE jobs ADD CONSTRAINT jobs_salary_annual_amount_chk CHECK (
            (salary_annual_min IS NULL OR salary_annual_min BETWEEN 0 AND 2000000)
            AND (salary_annual_max IS NULL OR salary_annual_max BETWEEN 0 AND 2000000)
        );
    END IF;
END $$;

COMMENT ON CONSTRAINT jobs_salary_interval_amount_chk ON jobs IS
    'Rejects interval/value mismatches before they can reach the public API';
$ddl$);

SELECT pg_temp.apply_job_feed_file(
    '0028_job_search_and_ingestion_updates.sql',
    '0e6eecb95cea966a00a1ab20589c4599dc4e1cc594d747ffb5b622906f30f219',
    $ddl$
ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS updated_count INTEGER NOT NULL DEFAULT 0;

-- array_to_string is stable rather than immutable in PostgreSQL, so wrap the
-- exact expression used by the generated search column in an immutable helper.
CREATE OR REPLACE FUNCTION immutable_text_array_join(values_ TEXT[])
RETURNS TEXT LANGUAGE SQL IMMUTABLE PARALLEL SAFE
AS $$ SELECT array_to_string(values_, ' ') $$;

DROP INDEX IF EXISTS idx_jobs_active_search;
ALTER TABLE jobs DROP COLUMN IF EXISTS search_document;
ALTER TABLE jobs ADD COLUMN search_document TSVECTOR GENERATED ALWAYS AS (
    setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
    setweight(to_tsvector('simple', coalesce(company_name, '')), 'A') ||
    setweight(to_tsvector('simple', coalesce(location_text, '')), 'B') ||
    setweight(to_tsvector('simple', immutable_text_array_join(skill_tags)), 'B')
) STORED;
CREATE INDEX idx_jobs_active_search ON jobs USING GIN (search_document)
WHERE status = 1;

COMMENT ON COLUMN jobs.search_document IS
    'Searches title, company, location and normalized skill tags; description remains excluded';
$ddl$);

SELECT pg_temp.apply_job_feed_file(
    '0030_collection_performance.sql',
    '908e666f301c0aa9f2dfaa4c072ab1a6f794bee5c525c000f399f7f25fa45d33',
    $ddl$
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
$ddl$);

SELECT pg_temp.apply_job_feed_file(
    'schema.sql',
    'f9f6ab912e944cbe0d7520a9f0799e3f6289901455507f3f9214c494a00dfed4',
    $ddl$
-- Applied inside the collector's private schema after the public ingestion migrations.
DROP TABLE IF EXISTS collection_checkpoints;
DROP TABLE IF EXISTS collection_plans CASCADE;
ALTER TABLE ingestion_runs DROP COLUMN IF EXISTS collection_plan_id;

CREATE TABLE feed_checkpoint (
    plan text NOT NULL,
    source text NOT NULL,
    campaign text NOT NULL,
    saved_at timestamptz NOT NULL DEFAULT now(),
    records bytea NOT NULL,
    PRIMARY KEY (plan, source)
);

-- Retain only the latest content hash/sequence per job. NULL is a pending tombstone.
CREATE SEQUENCE feed_release_seq;
CREATE TABLE feed_versions (
    public_id uuid PRIMARY KEY,
    content_hash bytea,
    sync_seq bigint NOT NULL
);
CREATE INDEX feed_versions_seq ON feed_versions(sync_seq);
$ddl$);

DROP FUNCTION pg_temp.apply_job_feed_file(text, text, text);
COMMIT;

SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'job_feed' ORDER BY tablename;
