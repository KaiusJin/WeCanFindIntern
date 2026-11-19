-- Final cloud job-feed schema. Run once in a fresh Supabase jobs database as postgres.
-- Prerequisite: CREATE ROLE job_collector LOGIN, with its password set separately.
-- Keep job_feed outside the Data API exposed schemas.

BEGIN;
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;
GRANT job_collector TO postgres;
CREATE SCHEMA job_feed AUTHORIZATION job_collector;
REVOKE ALL ON SCHEMA job_feed FROM PUBLIC, anon, authenticated;
GRANT USAGE ON SCHEMA extensions TO job_collector;
DO $$ BEGIN
    EXECUTE format('GRANT TEMPORARY ON DATABASE %I TO job_collector', current_database());
END $$;
ALTER ROLE job_collector SET search_path = job_feed, extensions;
SET LOCAL ROLE job_collector;
SET LOCAL search_path = job_feed, extensions;

CREATE FUNCTION job_feed.ensure_raw_job_snapshot_partition(target_time timestamp with time zone) RETURNS void
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

CREATE FUNCTION job_feed.immutable_text_array_join(values_ text[]) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    AS $$ SELECT array_to_string(values_, ' ') $$;

CREATE TABLE job_feed.dedupe_candidates (
    id bigint NOT NULL,
    incoming_job_id bigint NOT NULL,
    candidate_job_id bigint NOT NULL,
    source_fingerprint bytea NOT NULL,
    score numeric(5,4) NOT NULL,
    evidence jsonb NOT NULL,
    status smallint DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    reviewed_at timestamp with time zone,
    CONSTRAINT dedupe_candidates_distinct_jobs_chk CHECK ((incoming_job_id <> candidate_job_id)),
    CONSTRAINT dedupe_candidates_score_chk CHECK (((score >= (0)::numeric) AND (score <= (1)::numeric))),
    CONSTRAINT dedupe_candidates_status_chk CHECK (((status >= 0) AND (status <= 3)))
);

ALTER TABLE job_feed.dedupe_candidates ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME job_feed.dedupe_candidates_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE job_feed.dedupe_decisions (
    id bigint NOT NULL,
    source_fingerprint bytea NOT NULL,
    resulting_job_id bigint NOT NULL,
    compared_job_id bigint,
    action text NOT NULL,
    score numeric(5,4) NOT NULL,
    algorithm_version smallint NOT NULL,
    evidence jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT dedupe_decisions_action_chk CHECK ((action = ANY (ARRAY['merge'::text, 'create'::text]))),
    CONSTRAINT dedupe_decisions_fingerprint_len_chk CHECK ((octet_length(source_fingerprint) = 32)),
    CONSTRAINT dedupe_decisions_score_chk CHECK (((score >= (0)::numeric) AND (score <= (1)::numeric)))
);

ALTER TABLE job_feed.dedupe_decisions ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME job_feed.dedupe_decisions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE job_feed.feed_checkpoint (
    plan text NOT NULL,
    source text NOT NULL,
    campaign text NOT NULL,
    saved_at timestamp with time zone DEFAULT now() NOT NULL,
    records bytea NOT NULL
);

CREATE SEQUENCE job_feed.feed_release_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE TABLE job_feed.feed_versions (
    public_id uuid NOT NULL,
    content_hash bytea,
    sync_seq bigint NOT NULL
);

CREATE TABLE job_feed.ingestion_runs (
    id bigint NOT NULL,
    public_id uuid DEFAULT gen_random_uuid() NOT NULL,
    provider text NOT NULL,
    sources text[] DEFAULT '{}'::text[] NOT NULL,
    query jsonb NOT NULL,
    status smallint DEFAULT 0 NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    fetched_count integer DEFAULT 0 NOT NULL,
    created_count integer DEFAULT 0 NOT NULL,
    merged_count integer DEFAULT 0 NOT NULL,
    unchanged_count integer DEFAULT 0 NOT NULL,
    review_count integer DEFAULT 0 NOT NULL,
    failed_count integer DEFAULT 0 NOT NULL,
    error_summary text,
    updated_count integer DEFAULT 0 NOT NULL,
    CONSTRAINT ingestion_runs_status_chk CHECK (((status >= 0) AND (status <= 3)))
);

ALTER TABLE job_feed.ingestion_runs ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME job_feed.ingestion_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE job_feed.job_sources (
    id bigint NOT NULL,
    job_id bigint NOT NULL,
    source text NOT NULL,
    source_job_id text,
    source_url text NOT NULL,
    canonical_source_url text NOT NULL,
    direct_url text,
    canonical_direct_url text,
    direct_url_hash bytea,
    source_fingerprint bytea NOT NULL,
    last_payload_hash bytea NOT NULL,
    first_seen_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone NOT NULL,
    last_scraped_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    details_fetched_at timestamp with time zone,
    CONSTRAINT job_sources_direct_hash_len_chk CHECK (((direct_url_hash IS NULL) OR (octet_length(direct_url_hash) = 32))),
    CONSTRAINT job_sources_fingerprint_len_chk CHECK ((octet_length(source_fingerprint) = 32)),
    CONSTRAINT job_sources_payload_hash_len_chk CHECK ((octet_length(last_payload_hash) = 32))
);

ALTER TABLE job_feed.job_sources ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME job_feed.job_sources_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE job_feed.job_status_history (
    id bigint NOT NULL,
    job_id bigint NOT NULL,
    from_status smallint,
    to_status smallint NOT NULL,
    reason text,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    ingestion_run_id bigint
);

ALTER TABLE job_feed.job_status_history ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME job_feed.job_status_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE job_feed.jobs (
    id bigint NOT NULL,
    public_id uuid DEFAULT gen_random_uuid() NOT NULL,
    title text NOT NULL,
    title_normalized text NOT NULL,
    company_name text,
    company_normalized text DEFAULT ''::text NOT NULL,
    company_industry text,
    company_website_url text,
    company_logo_url text,
    location_text text,
    city text,
    region_code text,
    country_code character(2),
    location_normalized text DEFAULT ''::text NOT NULL,
    work_mode text DEFAULT 'unknown'::text NOT NULL,
    employment_types text[] DEFAULT '{}'::text[] NOT NULL,
    primary_employment_type text,
    date_posted date,
    published_sort_at timestamp with time zone NOT NULL,
    seniority text,
    job_function text,
    description text,
    description_hash bytea,
    salary_interval text,
    salary_min numeric(14,2),
    salary_max numeric(14,2),
    salary_currency character(3),
    salary_source text,
    source_skills text[] DEFAULT '{}'::text[] NOT NULL,
    contact_emails text[] DEFAULT '{}'::text[] NOT NULL,
    experience_range text,
    vacancy_count integer,
    dedupe_block_key bytea NOT NULL,
    status smallint DEFAULT 1 NOT NULL,
    source_count integer DEFAULT 0 NOT NULL,
    first_seen_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone NOT NULL,
    last_verified_at timestamp with time zone NOT NULL,
    closed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    opportunity_type text DEFAULT 'unknown'::text NOT NULL,
    schedule_types text[] DEFAULT '{}'::text[] NOT NULL,
    primary_schedule_type text DEFAULT 'unknown'::text NOT NULL,
    job_category text DEFAULT 'other'::text NOT NULL,
    job_subcategories text[] DEFAULT '{}'::text[] NOT NULL,
    seniority_level text DEFAULT 'unknown'::text NOT NULL,
    education_levels text[] DEFAULT '{}'::text[] NOT NULL,
    experience_min_years smallint,
    experience_max_years smallint,
    skill_tags text[] DEFAULT '{}'::text[] NOT NULL,
    requirement_tags text[] DEFAULT '{}'::text[] NOT NULL,
    display_tags text[] DEFAULT '{}'::text[] NOT NULL,
    classification_version smallint DEFAULT 0 NOT NULL,
    salary_annual_min numeric(14,2),
    salary_annual_max numeric(14,2),
    region_name text,
    region_type text,
    country_name text,
    recruiting_season text,
    recruiting_year smallint,
    recruiting_term_source text,
    recruiting_term_evidence text,
    recruiting_term_input_hash bytea,
    recruiting_term_checked_at timestamp with time zone,
    recruiting_term_model text,
    search_document tsvector GENERATED ALWAYS AS ((((setweight(to_tsvector('simple'::regconfig, COALESCE(title, ''::text)), 'A'::"char") || setweight(to_tsvector('simple'::regconfig, COALESCE(company_name, ''::text)), 'A'::"char")) || setweight(to_tsvector('simple'::regconfig, COALESCE(location_text, ''::text)), 'B'::"char")) || setweight(to_tsvector('simple'::regconfig, job_feed.immutable_text_array_join(skill_tags)), 'B'::"char"))) STORED,
    salary_enrichment_input_hash bytea,
    salary_enrichment_status text,
    salary_enrichment_checked_at timestamp with time zone,
    salary_enrichment_model text,
    CONSTRAINT jobs_annual_salary_order_chk CHECK (((salary_annual_min IS NULL) OR (salary_annual_max IS NULL) OR (salary_annual_min <= salary_annual_max))),
    CONSTRAINT jobs_experience_years_chk CHECK ((((experience_min_years IS NULL) OR ((experience_min_years >= 0) AND (experience_min_years <= 30))) AND ((experience_max_years IS NULL) OR ((experience_max_years >= 0) AND (experience_max_years <= 30))) AND ((experience_min_years IS NULL) OR (experience_max_years IS NULL) OR (experience_min_years <= experience_max_years)))),
    CONSTRAINT jobs_job_category_chk CHECK ((job_category = ANY (ARRAY['software_engineering'::text, 'data_ai'::text, 'cybersecurity'::text, 'cloud_devops'::text, 'qa_testing'::text, 'product_design'::text, 'product_management'::text, 'it_support'::text, 'hardware_embedded'::text, 'research'::text, 'business_operations'::text, 'finance'::text, 'marketing_sales'::text, 'human_resources'::text, 'healthcare'::text, 'education'::text, 'skilled_trades'::text, 'engineering'::text, 'architecture_planning'::text, 'legal'::text, 'customer_service'::text, 'supply_chain'::text, 'administrative'::text, 'other'::text]))),
    CONSTRAINT jobs_opportunity_type_chk CHECK ((opportunity_type = ANY (ARRAY['internship'::text, 'co_op'::text, 'new_grad'::text, 'apprenticeship'::text, 'regular'::text, 'contract'::text, 'temporary'::text, 'seasonal'::text, 'unknown'::text]))),
    CONSTRAINT jobs_primary_schedule_type_chk CHECK ((primary_schedule_type = ANY (ARRAY['full_time'::text, 'part_time'::text, 'flexible'::text, 'unknown'::text]))),
    CONSTRAINT jobs_recruiting_season_chk CHECK (((recruiting_season IS NULL) OR (recruiting_season = ANY (ARRAY['winter'::text, 'spring'::text, 'summer'::text, 'fall'::text])))),
    CONSTRAINT jobs_recruiting_year_chk CHECK (((recruiting_year IS NULL) OR ((recruiting_year >= 2020) AND (recruiting_year <= 2099)))),
    CONSTRAINT jobs_region_type_chk CHECK (((region_type IS NULL) OR (region_type = ANY (ARRAY['province'::text, 'territory'::text, 'state'::text, 'region'::text])))),
    CONSTRAINT jobs_salary_annual_amount_chk CHECK ((((salary_annual_min IS NULL) OR ((salary_annual_min >= (0)::numeric) AND (salary_annual_min <= (2000000)::numeric))) AND ((salary_annual_max IS NULL) OR ((salary_annual_max >= (0)::numeric) AND (salary_annual_max <= (2000000)::numeric))))),
    CONSTRAINT jobs_salary_enrichment_status_chk CHECK (((salary_enrichment_status IS NULL) OR (salary_enrichment_status = ANY (ARRAY['complete'::text, 'not_found'::text, 'error'::text])))),
    CONSTRAINT jobs_salary_interval_amount_chk CHECK (((salary_interval IS NULL) OR ((salary_interval = 'hourly'::text) AND ((salary_min IS NULL) OR ((salary_min >= (5)::numeric) AND (salary_min <= (500)::numeric))) AND ((salary_max IS NULL) OR ((salary_max >= (5)::numeric) AND (salary_max <= (500)::numeric)))) OR ((salary_interval = 'daily'::text) AND ((salary_min IS NULL) OR ((salary_min >= (40)::numeric) AND (salary_min <= (5000)::numeric))) AND ((salary_max IS NULL) OR ((salary_max >= (40)::numeric) AND (salary_max <= (5000)::numeric)))) OR ((salary_interval = 'weekly'::text) AND ((salary_min IS NULL) OR ((salary_min >= (100)::numeric) AND (salary_min <= (25000)::numeric))) AND ((salary_max IS NULL) OR ((salary_max >= (100)::numeric) AND (salary_max <= (25000)::numeric)))) OR ((salary_interval = 'monthly'::text) AND ((salary_min IS NULL) OR ((salary_min >= (500)::numeric) AND (salary_min <= (100000)::numeric))) AND ((salary_max IS NULL) OR ((salary_max >= (500)::numeric) AND (salary_max <= (100000)::numeric)))) OR ((salary_interval = 'yearly'::text) AND ((salary_min IS NULL) OR ((salary_min >= (5000)::numeric) AND (salary_min <= (2000000)::numeric))) AND ((salary_max IS NULL) OR ((salary_max >= (5000)::numeric) AND (salary_max <= (2000000)::numeric)))))),
    CONSTRAINT jobs_salary_order_chk CHECK (((salary_min IS NULL) OR (salary_max IS NULL) OR (salary_min <= salary_max))),
    CONSTRAINT jobs_seniority_level_chk CHECK ((seniority_level = ANY (ARRAY['student'::text, 'entry_level'::text, 'junior'::text, 'mid_level'::text, 'senior'::text, 'lead'::text, 'manager'::text, 'director'::text, 'executive'::text, 'unknown'::text]))),
    CONSTRAINT jobs_source_count_chk CHECK ((source_count >= 0)),
    CONSTRAINT jobs_status_chk CHECK (((status >= 1) AND (status <= 4))),
    CONSTRAINT jobs_vacancy_count_chk CHECK (((vacancy_count IS NULL) OR (vacancy_count >= 0))),
    CONSTRAINT jobs_work_mode_chk CHECK ((work_mode = ANY (ARRAY['onsite'::text, 'hybrid'::text, 'remote'::text, 'unknown'::text])))
);

ALTER TABLE job_feed.jobs ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME job_feed.jobs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE job_feed.raw_job_snapshots (
    id bigint NOT NULL,
    scraped_at timestamp with time zone NOT NULL,
    ingestion_run_id bigint NOT NULL,
    job_source_id bigint NOT NULL,
    source_fingerprint bytea NOT NULL,
    payload_hash bytea NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT raw_snapshots_fingerprint_len_chk CHECK ((octet_length(source_fingerprint) = 32)),
    CONSTRAINT raw_snapshots_payload_hash_len_chk CHECK ((octet_length(payload_hash) = 32))
)
PARTITION BY RANGE (scraped_at);

CREATE TABLE job_feed.raw_job_snapshots_default (
    id bigint NOT NULL,
    scraped_at timestamp with time zone NOT NULL,
    ingestion_run_id bigint NOT NULL,
    job_source_id bigint NOT NULL,
    source_fingerprint bytea NOT NULL,
    payload_hash bytea NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT raw_snapshots_fingerprint_len_chk CHECK ((octet_length(source_fingerprint) = 32)),
    CONSTRAINT raw_snapshots_payload_hash_len_chk CHECK ((octet_length(payload_hash) = 32))
);

ALTER TABLE job_feed.raw_job_snapshots ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME job_feed.raw_job_snapshots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE job_feed.recruiting_term_generations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_id bigint NOT NULL,
    input_hash bytea NOT NULL,
    input_context text NOT NULL,
    model text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    response_json jsonb,
    prompt_tokens integer,
    completion_tokens integer,
    error_type text,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    CONSTRAINT recruiting_term_generations_status_chk CHECK ((status = ANY (ARRAY['pending'::text, 'complete'::text, 'error'::text])))
);

ALTER TABLE ONLY job_feed.raw_job_snapshots ATTACH PARTITION job_feed.raw_job_snapshots_default DEFAULT;

ALTER TABLE ONLY job_feed.dedupe_candidates
    ADD CONSTRAINT dedupe_candidates_pkey PRIMARY KEY (id);

ALTER TABLE ONLY job_feed.dedupe_decisions
    ADD CONSTRAINT dedupe_decisions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY job_feed.feed_checkpoint
    ADD CONSTRAINT feed_checkpoint_pkey PRIMARY KEY (plan, source);

ALTER TABLE ONLY job_feed.feed_versions
    ADD CONSTRAINT feed_versions_pkey PRIMARY KEY (public_id);

ALTER TABLE ONLY job_feed.ingestion_runs
    ADD CONSTRAINT ingestion_runs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY job_feed.ingestion_runs
    ADD CONSTRAINT ingestion_runs_public_id_key UNIQUE (public_id);

ALTER TABLE ONLY job_feed.job_sources
    ADD CONSTRAINT job_sources_pkey PRIMARY KEY (id);

ALTER TABLE ONLY job_feed.job_status_history
    ADD CONSTRAINT job_status_history_pkey PRIMARY KEY (id);

ALTER TABLE ONLY job_feed.jobs
    ADD CONSTRAINT jobs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY job_feed.jobs
    ADD CONSTRAINT jobs_public_id_key UNIQUE (public_id);

ALTER TABLE ONLY job_feed.recruiting_term_generations
    ADD CONSTRAINT recruiting_term_generations_pkey PRIMARY KEY (id);

CREATE INDEX feed_versions_seq ON job_feed.feed_versions USING btree (sync_seq);

CREATE INDEX idx_dedupe_candidates_pending ON job_feed.dedupe_candidates USING btree (score DESC, id) WHERE (status = 0);

CREATE INDEX idx_dedupe_decisions_result_time ON job_feed.dedupe_decisions USING btree (resulting_job_id, created_at DESC);

CREATE INDEX idx_dedupe_decisions_source_time ON job_feed.dedupe_decisions USING btree (source_fingerprint, created_at DESC);

CREATE INDEX idx_ingestion_runs_started ON job_feed.ingestion_runs USING btree (started_at DESC, id DESC);

CREATE INDEX idx_job_sources_direct_hash ON job_feed.job_sources USING btree (direct_url_hash, job_id) WHERE (direct_url_hash IS NOT NULL);

CREATE INDEX idx_job_sources_job ON job_feed.job_sources USING btree (job_id, id);

CREATE INDEX idx_job_sources_linkedin_detail_freshness ON job_feed.job_sources USING btree (details_fetched_at DESC) WHERE (source = 'linkedin'::text);

CREATE INDEX idx_job_sources_source_job ON job_feed.job_sources USING btree (source, job_id);

CREATE INDEX idx_job_status_history_job_time ON job_feed.job_status_history USING btree (job_id, changed_at DESC, id DESC);

CREATE INDEX idx_jobs_active_annual_salary ON job_feed.jobs USING btree (salary_currency, salary_annual_max, published_sort_at DESC, id DESC) WHERE ((status = 1) AND (salary_annual_max IS NOT NULL));

CREATE INDEX idx_jobs_active_category_feed ON job_feed.jobs USING btree (job_category, published_sort_at DESC, id DESC) WHERE (status = 1);

CREATE INDEX idx_jobs_active_company_feed ON job_feed.jobs USING btree (company_normalized, published_sort_at DESC, id DESC) WHERE (status = 1);

CREATE INDEX idx_jobs_active_country_region_feed ON job_feed.jobs USING btree (country_code, region_code, published_sort_at DESC, id DESC) WHERE (status = 1);

CREATE INDEX idx_jobs_active_dedupe_block ON job_feed.jobs USING btree (dedupe_block_key, date_posted DESC, id DESC) WHERE (status = 1);

CREATE INDEX idx_jobs_active_employment_feed ON job_feed.jobs USING btree (primary_employment_type, published_sort_at DESC, id DESC) WHERE (status = 1);

CREATE INDEX idx_jobs_active_feed ON job_feed.jobs USING btree (published_sort_at DESC, id DESC) WHERE (status = 1);

CREATE INDEX idx_jobs_active_location_hierarchy_feed ON job_feed.jobs USING btree (country_code, region_code, city, published_sort_at DESC, id DESC) WHERE (status = 1);

CREATE INDEX idx_jobs_active_opportunity_feed ON job_feed.jobs USING btree (opportunity_type, published_sort_at DESC, id DESC) WHERE (status = 1);

CREATE INDEX idx_jobs_active_posted ON job_feed.jobs USING btree (date_posted DESC, id DESC) WHERE ((status = 1) AND (date_posted IS NOT NULL));

CREATE INDEX idx_jobs_active_recruiting_term ON job_feed.jobs USING btree (recruiting_year, recruiting_season, published_sort_at DESC, id DESC) WHERE ((status = 1) AND (recruiting_season IS NOT NULL));

CREATE INDEX idx_jobs_active_salary ON job_feed.jobs USING btree (salary_currency, salary_max, published_sort_at DESC, id DESC) WHERE ((status = 1) AND (salary_max IS NOT NULL));

CREATE INDEX idx_jobs_active_schedule_feed ON job_feed.jobs USING btree (primary_schedule_type, published_sort_at DESC, id DESC) WHERE (status = 1);

CREATE INDEX idx_jobs_active_search ON job_feed.jobs USING gin (search_document) WHERE (status = 1);

CREATE INDEX idx_jobs_active_seniority_feed ON job_feed.jobs USING btree (seniority_level, published_sort_at DESC, id DESC) WHERE (status = 1);

CREATE INDEX idx_jobs_active_skill_tags ON job_feed.jobs USING gin (skill_tags) WHERE (status = 1);

CREATE INDEX idx_jobs_active_subcategories ON job_feed.jobs USING gin (job_subcategories) WHERE (status = 1);

CREATE INDEX idx_jobs_active_work_mode_feed ON job_feed.jobs USING btree (work_mode, published_sort_at DESC, id DESC) WHERE (status = 1);

CREATE INDEX idx_jobs_description_hash ON job_feed.jobs USING btree (description_hash) WHERE (description_hash IS NOT NULL);

CREATE INDEX idx_raw_snapshots_run ON ONLY job_feed.raw_job_snapshots USING btree (ingestion_run_id, id);

CREATE INDEX idx_raw_snapshots_scraped_brin ON ONLY job_feed.raw_job_snapshots USING brin (scraped_at) WITH (pages_per_range='128');

CREATE INDEX idx_raw_snapshots_source_time ON ONLY job_feed.raw_job_snapshots USING btree (job_source_id, scraped_at DESC);

CREATE INDEX idx_recruiting_term_generations_job ON job_feed.recruiting_term_generations USING btree (job_id, started_at DESC);

CREATE INDEX raw_job_snapshots_default_ingestion_run_id_id_idx ON job_feed.raw_job_snapshots_default USING btree (ingestion_run_id, id);

CREATE INDEX raw_job_snapshots_default_job_source_id_scraped_at_idx ON job_feed.raw_job_snapshots_default USING btree (job_source_id, scraped_at DESC);

CREATE INDEX raw_job_snapshots_default_scraped_at_idx ON job_feed.raw_job_snapshots_default USING brin (scraped_at) WITH (pages_per_range='128');

CREATE UNIQUE INDEX uq_dedupe_candidates_pair ON job_feed.dedupe_candidates USING btree (incoming_job_id, candidate_job_id);

CREATE UNIQUE INDEX uq_job_sources_fingerprint ON job_feed.job_sources USING btree (source_fingerprint);

CREATE UNIQUE INDEX uq_job_sources_source_job_id ON job_feed.job_sources USING btree (source, source_job_id) WHERE (source_job_id IS NOT NULL);

ALTER INDEX job_feed.idx_raw_snapshots_run ATTACH PARTITION job_feed.raw_job_snapshots_default_ingestion_run_id_id_idx;

ALTER INDEX job_feed.idx_raw_snapshots_source_time ATTACH PARTITION job_feed.raw_job_snapshots_default_job_source_id_scraped_at_idx;

ALTER INDEX job_feed.idx_raw_snapshots_scraped_brin ATTACH PARTITION job_feed.raw_job_snapshots_default_scraped_at_idx;

ALTER TABLE ONLY job_feed.dedupe_candidates
    ADD CONSTRAINT dedupe_candidates_candidate_job_id_fkey FOREIGN KEY (candidate_job_id) REFERENCES job_feed.jobs(id) ON DELETE CASCADE;

ALTER TABLE ONLY job_feed.dedupe_candidates
    ADD CONSTRAINT dedupe_candidates_incoming_job_id_fkey FOREIGN KEY (incoming_job_id) REFERENCES job_feed.jobs(id) ON DELETE CASCADE;

ALTER TABLE ONLY job_feed.dedupe_decisions
    ADD CONSTRAINT dedupe_decisions_compared_job_id_fkey FOREIGN KEY (compared_job_id) REFERENCES job_feed.jobs(id) ON DELETE SET NULL;

ALTER TABLE ONLY job_feed.dedupe_decisions
    ADD CONSTRAINT dedupe_decisions_resulting_job_id_fkey FOREIGN KEY (resulting_job_id) REFERENCES job_feed.jobs(id) ON DELETE CASCADE;

ALTER TABLE ONLY job_feed.job_sources
    ADD CONSTRAINT job_sources_job_id_fkey FOREIGN KEY (job_id) REFERENCES job_feed.jobs(id) ON DELETE CASCADE;

ALTER TABLE ONLY job_feed.job_status_history
    ADD CONSTRAINT job_status_history_ingestion_run_id_fkey FOREIGN KEY (ingestion_run_id) REFERENCES job_feed.ingestion_runs(id) ON DELETE SET NULL;

ALTER TABLE ONLY job_feed.job_status_history
    ADD CONSTRAINT job_status_history_job_id_fkey FOREIGN KEY (job_id) REFERENCES job_feed.jobs(id) ON DELETE CASCADE;

ALTER TABLE job_feed.raw_job_snapshots
    ADD CONSTRAINT raw_job_snapshots_ingestion_run_id_fkey FOREIGN KEY (ingestion_run_id) REFERENCES job_feed.ingestion_runs(id) ON DELETE RESTRICT;

ALTER TABLE job_feed.raw_job_snapshots
    ADD CONSTRAINT raw_job_snapshots_job_source_id_fkey FOREIGN KEY (job_source_id) REFERENCES job_feed.job_sources(id) ON DELETE CASCADE;

ALTER TABLE ONLY job_feed.recruiting_term_generations
    ADD CONSTRAINT recruiting_term_generations_job_id_fkey FOREIGN KEY (job_id) REFERENCES job_feed.jobs(id) ON DELETE CASCADE;

COMMIT;
