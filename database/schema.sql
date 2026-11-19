-- Final desktop database schema. Apply once to an empty PostgreSQL database.
-- Requires pgcrypto, pg_trgm and vector extensions.

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

CREATE FUNCTION public.enqueue_recommendation_document() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.status = 1 THEN
        INSERT INTO recommendation_index_queue (public_job_id,queued_at,attempts,last_error)
        VALUES (NEW.public_id,now(),0,NULL)
        ON CONFLICT (public_job_id) DO UPDATE SET
            queued_at=EXCLUDED.queued_at,attempts=0,last_error=NULL;
    ELSE
        DELETE FROM recommendation_index_queue WHERE public_job_id=NEW.public_id;
        DELETE FROM recommendation_documents
        WHERE source='public' AND public_job_id=NEW.public_id;
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.ensure_raw_job_snapshot_partition(target_time timestamp with time zone) RETURNS void
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

CREATE FUNCTION public.immutable_text_array_join(values_ text[]) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    AS $$ SELECT array_to_string(values_, ' ') $$;

CREATE TABLE public.agent_approvals (
    id bigint NOT NULL,
    public_id uuid DEFAULT gen_random_uuid() NOT NULL,
    session_id bigint NOT NULL,
    tool_name character varying(64) NOT NULL,
    arguments jsonb DEFAULT '{}'::jsonb NOT NULL,
    preview jsonb DEFAULT '{}'::jsonb NOT NULL,
    status character varying(16) DEFAULT 'pending'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    decided_at timestamp with time zone,
    CONSTRAINT agent_approvals_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'approved'::character varying, 'denied'::character varying])::text[])))
);

CREATE SEQUENCE public.agent_approvals_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.agent_approvals_id_seq OWNED BY public.agent_approvals.id;

CREATE TABLE public.agent_audit_log (
    id bigint NOT NULL,
    session_id bigint,
    user_intent text,
    tool_name character varying(64),
    arguments_summary text,
    approval_status character varying(16),
    result_summary text,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.agent_audit_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.agent_audit_log_id_seq OWNED BY public.agent_audit_log.id;

CREATE TABLE public.agent_conversation_summaries (
    id bigint NOT NULL,
    session_id bigint NOT NULL,
    version integer NOT NULL,
    summary_text text NOT NULL,
    summary_json jsonb NOT NULL,
    token_count integer DEFAULT 0 NOT NULL,
    covered_message_count integer DEFAULT 0 NOT NULL,
    covers_through_message_id uuid,
    provider character varying(32),
    model character varying(128),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.agent_conversation_summaries_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.agent_conversation_summaries_id_seq OWNED BY public.agent_conversation_summaries.id;

CREATE TABLE public.agent_memories (
    id bigint NOT NULL,
    public_id uuid DEFAULT gen_random_uuid() NOT NULL,
    session_id bigint,
    memory_type character varying(32) NOT NULL,
    content text NOT NULL,
    content_hash character varying(64) NOT NULL,
    confidence numeric(4,3) DEFAULT 1.0 NOT NULL,
    status character varying(16) DEFAULT 'ACTIVE'::character varying NOT NULL,
    source_message_id uuid,
    superseded_by uuid,
    access_count integer DEFAULT 0 NOT NULL,
    last_accessed_at timestamp with time zone,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT agent_memories_memory_type_check CHECK (((memory_type)::text = ANY ((ARRAY['USER_PREFERENCE'::character varying, 'CAREER_CONTEXT'::character varying, 'JOB_TARGET'::character varying, 'EXPLICIT_FACT'::character varying, 'SKILL_PROFILE'::character varying, 'EDUCATION_PROFILE'::character varying, 'WORK_EXPERIENCE'::character varying, 'APPLICATION_PLAN'::character varying])::text[]))),
    CONSTRAINT agent_memories_status_check CHECK (((status)::text = ANY ((ARRAY['ACTIVE'::character varying, 'SUPERSEDED'::character varying, 'EXPIRED'::character varying])::text[])))
);

CREATE SEQUENCE public.agent_memories_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.agent_memories_id_seq OWNED BY public.agent_memories.id;

CREATE TABLE public.agent_messages (
    id bigint NOT NULL,
    public_id uuid DEFAULT gen_random_uuid() NOT NULL,
    session_id bigint NOT NULL,
    role character varying(16) NOT NULL,
    content text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    token_count integer DEFAULT 0 NOT NULL,
    CONSTRAINT agent_messages_role_check CHECK (((role)::text = ANY ((ARRAY['user'::character varying, 'assistant'::character varying])::text[])))
);

CREATE SEQUENCE public.agent_messages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.agent_messages_id_seq OWNED BY public.agent_messages.id;

CREATE TABLE public.agent_sessions (
    id bigint NOT NULL,
    public_id uuid DEFAULT gen_random_uuid() NOT NULL,
    title character varying(255) DEFAULT 'New conversation'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    summary_text text,
    summary_json jsonb,
    summary_version integer DEFAULT 0 NOT NULL,
    summary_token_count integer DEFAULT 0 NOT NULL,
    summary_covers_through_message_id uuid,
    extraction_covers_through_message_id uuid,
    last_message_at timestamp with time zone
);

CREATE SEQUENCE public.agent_sessions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.agent_sessions_id_seq OWNED BY public.agent_sessions.id;

CREATE TABLE public.agent_tool_calls (
    id bigint NOT NULL,
    public_id uuid DEFAULT gen_random_uuid() NOT NULL,
    session_id bigint NOT NULL,
    message_id bigint,
    tool_name character varying(64) NOT NULL,
    arguments jsonb DEFAULT '{}'::jsonb NOT NULL,
    status character varying(24) DEFAULT 'succeeded'::character varying NOT NULL,
    result jsonb,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT agent_tool_calls_status_check CHECK (((status)::text = ANY ((ARRAY['succeeded'::character varying, 'failed'::character varying, 'awaiting_approval'::character varying])::text[])))
);

CREATE SEQUENCE public.agent_tool_calls_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.agent_tool_calls_id_seq OWNED BY public.agent_tool_calls.id;

CREATE TABLE public.agent_user_preferences (
    id bigint NOT NULL,
    preference_key character varying(64) NOT NULL,
    preference_value text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.agent_user_preferences_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.agent_user_preferences_id_seq OWNED BY public.agent_user_preferences.id;

CREATE TABLE public.application_tracker (
    id bigint NOT NULL,
    public_id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_id uuid,
    company_name character varying(255) NOT NULL,
    title character varying(255) NOT NULL,
    location_text character varying(255),
    work_mode character varying(64),
    job_url text,
    job_description text,
    salary_text character varying(128),
    origin_type character varying(32) DEFAULT 'custom'::character varying NOT NULL,
    source_type character varying(32) DEFAULT 'other'::character varying NOT NULL,
    stage character varying(32) DEFAULT 'interested'::character varying NOT NULL,
    applied_at timestamp with time zone,
    interview_at timestamp with time zone,
    offer_at timestamp with time zone,
    rejected_at timestamp with time zone,
    application_deadline date,
    follow_up_at timestamp with time zone,
    source_text character varying(128),
    priority character varying(16) DEFAULT 'normal'::character varying NOT NULL,
    next_step text,
    archived_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    external_job_id character varying(255),
    external_stage character varying(32),
    external_status character varying(128),
    CONSTRAINT application_tracker_external_stage_chk CHECK (((external_stage IS NULL) OR ((external_stage)::text = ANY ((ARRAY['interested'::character varying, 'applied'::character varying, 'interview'::character varying, 'offer'::character varying, 'rejected'::character varying])::text[])))),
    CONSTRAINT application_tracker_origin_type_chk CHECK (((origin_type)::text = ANY ((ARRAY['platform_bookmark'::character varying, 'custom'::character varying])::text[]))),
    CONSTRAINT application_tracker_source_type_chk CHECK (((source_type)::text = ANY ((ARRAY['wecanfindintern'::character varying, 'linkedin'::character varying, 'indeed'::character varying, 'glassdoor'::character varying, 'zip_recruiter'::character varying, 'google'::character varying, 'waterloo_work'::character varying, 'other'::character varying])::text[])))
);

CREATE TABLE public.application_tracker_events (
    id bigint NOT NULL,
    public_id uuid DEFAULT gen_random_uuid() NOT NULL,
    application_id bigint NOT NULL,
    event_type character varying(32) NOT NULL,
    title character varying(255) NOT NULL,
    details text,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT application_tracker_event_type_no_note CHECK (((event_type)::text <> 'note'::text))
);

CREATE SEQUENCE public.application_tracker_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.application_tracker_events_id_seq OWNED BY public.application_tracker_events.id;

CREATE SEQUENCE public.application_tracker_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.application_tracker_id_seq OWNED BY public.application_tracker.id;

CREATE TABLE public.dedupe_candidates (
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

ALTER TABLE public.dedupe_candidates ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.dedupe_candidates_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE public.dedupe_decisions (
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

ALTER TABLE public.dedupe_decisions ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.dedupe_decisions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE public.ingestion_runs (
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

ALTER TABLE public.ingestion_runs ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.ingestion_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE public.interview_answers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    session_id uuid NOT NULL,
    question_index integer NOT NULL,
    question_text text DEFAULT ''::text NOT NULL,
    answer_text text DEFAULT ''::text NOT NULL,
    transcript text DEFAULT ''::text NOT NULL,
    transcript_language text DEFAULT ''::text NOT NULL,
    duration_seconds double precision DEFAULT 0 NOT NULL,
    score integer DEFAULT 0 NOT NULL,
    summary text DEFAULT ''::text NOT NULL,
    star_feedback text DEFAULT ''::text NOT NULL,
    timeline jsonb DEFAULT '[]'::jsonb NOT NULL,
    advice jsonb DEFAULT '[]'::jsonb NOT NULL,
    provider text DEFAULT ''::text NOT NULL,
    model_name text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    criteria_results jsonb DEFAULT '[]'::jsonb NOT NULL,
    CONSTRAINT interview_answers_question_index_nonnegative CHECK ((question_index >= 0)),
    CONSTRAINT interview_answers_score_check CHECK (((score >= 0) AND (score <= 100)))
);

CREATE TABLE public.interview_sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_description text NOT NULL,
    provider text DEFAULT ''::text NOT NULL,
    model_name text DEFAULT ''::text NOT NULL,
    questions jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.job_sources (
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

ALTER TABLE public.job_sources ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.job_sources_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE public.job_status_history (
    id bigint NOT NULL,
    job_id bigint NOT NULL,
    from_status smallint,
    to_status smallint NOT NULL,
    reason text,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    ingestion_run_id bigint
);

ALTER TABLE public.job_status_history ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.job_status_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE public.jobs (
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
    search_document tsvector GENERATED ALWAYS AS ((((setweight(to_tsvector('simple'::regconfig, COALESCE(title, ''::text)), 'A'::"char") || setweight(to_tsvector('simple'::regconfig, COALESCE(company_name, ''::text)), 'A'::"char")) || setweight(to_tsvector('simple'::regconfig, COALESCE(location_text, ''::text)), 'B'::"char")) || setweight(to_tsvector('simple'::regconfig, public.immutable_text_array_join(skill_tags)), 'B'::"char"))) STORED,
    salary_enrichment_input_hash bytea,
    salary_enrichment_status text,
    salary_enrichment_checked_at timestamp with time zone,
    salary_enrichment_model text,
    cloud_id uuid,
    CONSTRAINT jobs_annual_salary_order_chk CHECK (((salary_annual_min IS NULL) OR (salary_annual_max IS NULL) OR (salary_annual_min <= salary_annual_max))),
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
    CONSTRAINT jobs_source_count_chk CHECK ((source_count >= 0)),
    CONSTRAINT jobs_status_chk CHECK (((status >= 1) AND (status <= 4))),
    CONSTRAINT jobs_vacancy_count_chk CHECK (((vacancy_count IS NULL) OR (vacancy_count >= 0))),
    CONSTRAINT jobs_work_mode_chk CHECK ((work_mode = ANY (ARRAY['onsite'::text, 'hybrid'::text, 'remote'::text, 'unknown'::text])))
);

ALTER TABLE public.jobs ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.jobs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE public.llm_cache (
    cache_key character(64) NOT NULL,
    provider text NOT NULL,
    model text NOT NULL,
    response text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.profile_awards (
    id bigint NOT NULL,
    public_id uuid DEFAULT gen_random_uuid() NOT NULL,
    profile_id bigint NOT NULL,
    "position" integer NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.profile_awards_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.profile_awards_id_seq OWNED BY public.profile_awards.id;

CREATE TABLE public.profile_certifications (
    id bigint NOT NULL,
    public_id uuid DEFAULT gen_random_uuid() NOT NULL,
    profile_id bigint NOT NULL,
    "position" integer NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.profile_certifications_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.profile_certifications_id_seq OWNED BY public.profile_certifications.id;

CREATE TABLE public.profile_education (
    id bigint NOT NULL,
    public_id uuid DEFAULT gen_random_uuid() NOT NULL,
    profile_id bigint NOT NULL,
    "position" integer NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.profile_education_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.profile_education_id_seq OWNED BY public.profile_education.id;

CREATE TABLE public.profile_imports (
    id bigint NOT NULL,
    public_id uuid DEFAULT gen_random_uuid() NOT NULL,
    profile_id bigint NOT NULL,
    resume_id bigint NOT NULL,
    parser_version character varying(64) NOT NULL,
    parsed_payload jsonb NOT NULL,
    warnings jsonb DEFAULT '[]'::jsonb NOT NULL,
    status character varying(16) DEFAULT 'draft'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    confirmed_at timestamp with time zone,
    CONSTRAINT profile_imports_status_check CHECK (((status)::text = ANY ((ARRAY['draft'::character varying, 'confirmed'::character varying, 'failed'::character varying])::text[])))
);

CREATE SEQUENCE public.profile_imports_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.profile_imports_id_seq OWNED BY public.profile_imports.id;

CREATE TABLE public.profile_languages (
    id bigint NOT NULL,
    public_id uuid DEFAULT gen_random_uuid() NOT NULL,
    profile_id bigint NOT NULL,
    "position" integer NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.profile_languages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.profile_languages_id_seq OWNED BY public.profile_languages.id;

CREATE TABLE public.profile_projects (
    id bigint NOT NULL,
    public_id uuid DEFAULT gen_random_uuid() NOT NULL,
    profile_id bigint NOT NULL,
    "position" integer NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.profile_projects_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.profile_projects_id_seq OWNED BY public.profile_projects.id;

CREATE TABLE public.profile_skills (
    id bigint NOT NULL,
    public_id uuid DEFAULT gen_random_uuid() NOT NULL,
    profile_id bigint NOT NULL,
    "position" integer NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.profile_skills_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.profile_skills_id_seq OWNED BY public.profile_skills.id;

CREATE TABLE public.profile_work_experience (
    id bigint NOT NULL,
    public_id uuid DEFAULT gen_random_uuid() NOT NULL,
    profile_id bigint NOT NULL,
    "position" integer NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.profile_work_experience_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.profile_work_experience_id_seq OWNED BY public.profile_work_experience.id;

CREATE TABLE public.public_job_sync (
    singleton boolean DEFAULT true NOT NULL,
    version bigint NOT NULL,
    CONSTRAINT public_job_sync_singleton_check CHECK (singleton),
    CONSTRAINT public_job_sync_version_check CHECK ((version >= 0))
);

CREATE TABLE public.raw_job_snapshots (
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

CREATE TABLE public.raw_job_snapshots_default (
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

ALTER TABLE public.raw_job_snapshots ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.raw_job_snapshots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE public.recommendation_chunk_embeddings (
    chunk_id bigint NOT NULL,
    provider text NOT NULL,
    model text NOT NULL,
    dimensions integer NOT NULL,
    embedding public.vector NOT NULL,
    embedding_version text NOT NULL,
    embedded_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT recommendation_chunk_embeddings_dimensions_check CHECK (((dimensions >= 1) AND (dimensions <= 4096))),
    CONSTRAINT recommendation_chunk_embeddings_provider_check CHECK ((provider = ANY (ARRAY['OpenAI'::text, 'Gemini'::text, 'Ollama'::text])))
);

CREATE TABLE public.recommendation_chunks (
    id bigint NOT NULL,
    document_id bigint NOT NULL,
    chunk_index integer NOT NULL,
    chunk_type text DEFAULT 'description'::text NOT NULL,
    chunk_text text NOT NULL,
    chunk_hash character(64) NOT NULL,
    CONSTRAINT recommendation_chunks_chunk_index_check CHECK ((chunk_index >= 0))
);

ALTER TABLE public.recommendation_chunks ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.recommendation_chunks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE public.recommendation_corpus_state (
    state_key text NOT NULL,
    corpus_version bigint DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.recommendation_documents (
    id bigint NOT NULL,
    source text NOT NULL,
    source_job_id text NOT NULL,
    public_job_id uuid,
    content_hash character(64) NOT NULL,
    title text NOT NULL,
    role_family text,
    normalized_skills text[] DEFAULT '{}'::text[] NOT NULL,
    requirement_tags text[] DEFAULT '{}'::text[] NOT NULL,
    document_text text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    index_version text DEFAULT 'recommend-document.v1'::text NOT NULL,
    indexed_at timestamp with time zone DEFAULT now() NOT NULL,
    search_document tsvector GENERATED ALWAYS AS (((setweight(to_tsvector('simple'::regconfig, COALESCE(title, ''::text)), 'A'::"char") || setweight(to_tsvector('simple'::regconfig, COALESCE(role_family, ''::text)), 'B'::"char")) || setweight(to_tsvector('simple'::regconfig, COALESCE(document_text, ''::text)), 'C'::"char"))) STORED,
    CONSTRAINT recommendation_documents_identity_chk CHECK ((((source = 'public'::text) AND (public_job_id IS NOT NULL) AND (source_job_id = (public_job_id)::text)) OR ((source = 'waterloo_work'::text) AND (public_job_id IS NULL)))),
    CONSTRAINT recommendation_documents_source_check CHECK ((source = ANY (ARRAY['public'::text, 'waterloo_work'::text])))
);

ALTER TABLE public.recommendation_documents ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.recommendation_documents_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE public.recommendation_index_queue (
    public_job_id uuid NOT NULL,
    queued_at timestamp with time zone DEFAULT now() NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    last_error text
);

CREATE TABLE public.recruiting_term_generations (
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

CREATE TABLE public.resume_documents (
    id bigint NOT NULL,
    public_id uuid DEFAULT gen_random_uuid() NOT NULL,
    profile_id bigint NOT NULL,
    filename character varying(255) NOT NULL,
    source_type character varying(16) NOT NULL,
    media_type character varying(100) NOT NULL,
    size_bytes integer NOT NULL,
    sha256 character(64) NOT NULL,
    content bytea NOT NULL,
    extracted_text text NOT NULL,
    parser_version character varying(64) NOT NULL,
    status character varying(16) DEFAULT 'draft'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    confirmed_at timestamp with time zone,
    CONSTRAINT resume_documents_size_bytes_check CHECK (((size_bytes > 0) AND (size_bytes <= 8388608))),
    CONSTRAINT resume_documents_source_type_check CHECK (((source_type)::text = ANY ((ARRAY['pdf'::character varying, 'latex'::character varying])::text[]))),
    CONSTRAINT resume_documents_status_check CHECK (((status)::text = ANY ((ARRAY['draft'::character varying, 'confirmed'::character varying, 'failed'::character varying])::text[])))
);

CREATE SEQUENCE public.resume_documents_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.resume_documents_id_seq OWNED BY public.resume_documents.id;

CREATE TABLE public.user_profiles (
    id bigint NOT NULL,
    public_id uuid DEFAULT gen_random_uuid() NOT NULL,
    profile_key character varying(64) DEFAULT 'default'::character varying NOT NULL,
    full_name character varying(200) DEFAULT ''::character varying NOT NULL,
    preferred_name character varying(120),
    email character varying(320),
    phone character varying(80),
    city character varying(120),
    region character varying(120),
    country character varying(120),
    linkedin_url text,
    github_url text,
    portfolio_url text,
    schema_version character varying(32) DEFAULT 'profile.v1'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.user_profiles_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.user_profiles_id_seq OWNED BY public.user_profiles.id;

ALTER TABLE ONLY public.raw_job_snapshots ATTACH PARTITION public.raw_job_snapshots_default DEFAULT;

ALTER TABLE ONLY public.agent_approvals ALTER COLUMN id SET DEFAULT nextval('public.agent_approvals_id_seq'::regclass);

ALTER TABLE ONLY public.agent_audit_log ALTER COLUMN id SET DEFAULT nextval('public.agent_audit_log_id_seq'::regclass);

ALTER TABLE ONLY public.agent_conversation_summaries ALTER COLUMN id SET DEFAULT nextval('public.agent_conversation_summaries_id_seq'::regclass);

ALTER TABLE ONLY public.agent_memories ALTER COLUMN id SET DEFAULT nextval('public.agent_memories_id_seq'::regclass);

ALTER TABLE ONLY public.agent_messages ALTER COLUMN id SET DEFAULT nextval('public.agent_messages_id_seq'::regclass);

ALTER TABLE ONLY public.agent_sessions ALTER COLUMN id SET DEFAULT nextval('public.agent_sessions_id_seq'::regclass);

ALTER TABLE ONLY public.agent_tool_calls ALTER COLUMN id SET DEFAULT nextval('public.agent_tool_calls_id_seq'::regclass);

ALTER TABLE ONLY public.agent_user_preferences ALTER COLUMN id SET DEFAULT nextval('public.agent_user_preferences_id_seq'::regclass);

ALTER TABLE ONLY public.application_tracker ALTER COLUMN id SET DEFAULT nextval('public.application_tracker_id_seq'::regclass);

ALTER TABLE ONLY public.application_tracker_events ALTER COLUMN id SET DEFAULT nextval('public.application_tracker_events_id_seq'::regclass);

ALTER TABLE ONLY public.profile_awards ALTER COLUMN id SET DEFAULT nextval('public.profile_awards_id_seq'::regclass);

ALTER TABLE ONLY public.profile_certifications ALTER COLUMN id SET DEFAULT nextval('public.profile_certifications_id_seq'::regclass);

ALTER TABLE ONLY public.profile_education ALTER COLUMN id SET DEFAULT nextval('public.profile_education_id_seq'::regclass);

ALTER TABLE ONLY public.profile_imports ALTER COLUMN id SET DEFAULT nextval('public.profile_imports_id_seq'::regclass);

ALTER TABLE ONLY public.profile_languages ALTER COLUMN id SET DEFAULT nextval('public.profile_languages_id_seq'::regclass);

ALTER TABLE ONLY public.profile_projects ALTER COLUMN id SET DEFAULT nextval('public.profile_projects_id_seq'::regclass);

ALTER TABLE ONLY public.profile_skills ALTER COLUMN id SET DEFAULT nextval('public.profile_skills_id_seq'::regclass);

ALTER TABLE ONLY public.profile_work_experience ALTER COLUMN id SET DEFAULT nextval('public.profile_work_experience_id_seq'::regclass);

ALTER TABLE ONLY public.resume_documents ALTER COLUMN id SET DEFAULT nextval('public.resume_documents_id_seq'::regclass);

ALTER TABLE ONLY public.user_profiles ALTER COLUMN id SET DEFAULT nextval('public.user_profiles_id_seq'::regclass);

ALTER TABLE ONLY public.agent_approvals
    ADD CONSTRAINT agent_approvals_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.agent_approvals
    ADD CONSTRAINT agent_approvals_public_id_key UNIQUE (public_id);

ALTER TABLE ONLY public.agent_audit_log
    ADD CONSTRAINT agent_audit_log_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.agent_conversation_summaries
    ADD CONSTRAINT agent_conversation_summaries_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.agent_conversation_summaries
    ADD CONSTRAINT agent_conversation_summaries_session_id_version_key UNIQUE (session_id, version);

ALTER TABLE ONLY public.agent_memories
    ADD CONSTRAINT agent_memories_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.agent_memories
    ADD CONSTRAINT agent_memories_public_id_key UNIQUE (public_id);

ALTER TABLE ONLY public.agent_messages
    ADD CONSTRAINT agent_messages_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.agent_messages
    ADD CONSTRAINT agent_messages_public_id_key UNIQUE (public_id);

ALTER TABLE ONLY public.agent_sessions
    ADD CONSTRAINT agent_sessions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.agent_sessions
    ADD CONSTRAINT agent_sessions_public_id_key UNIQUE (public_id);

ALTER TABLE ONLY public.agent_tool_calls
    ADD CONSTRAINT agent_tool_calls_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.agent_tool_calls
    ADD CONSTRAINT agent_tool_calls_public_id_key UNIQUE (public_id);

ALTER TABLE ONLY public.agent_user_preferences
    ADD CONSTRAINT agent_user_preferences_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.agent_user_preferences
    ADD CONSTRAINT agent_user_preferences_preference_key_key UNIQUE (preference_key);

ALTER TABLE ONLY public.application_tracker_events
    ADD CONSTRAINT application_tracker_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.application_tracker_events
    ADD CONSTRAINT application_tracker_events_public_id_key UNIQUE (public_id);

ALTER TABLE ONLY public.application_tracker
    ADD CONSTRAINT application_tracker_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.application_tracker
    ADD CONSTRAINT application_tracker_public_id_key UNIQUE (public_id);

ALTER TABLE ONLY public.dedupe_candidates
    ADD CONSTRAINT dedupe_candidates_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.dedupe_decisions
    ADD CONSTRAINT dedupe_decisions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.ingestion_runs
    ADD CONSTRAINT ingestion_runs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.ingestion_runs
    ADD CONSTRAINT ingestion_runs_public_id_key UNIQUE (public_id);

ALTER TABLE ONLY public.interview_answers
    ADD CONSTRAINT interview_answers_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.interview_answers
    ADD CONSTRAINT interview_answers_session_question_key UNIQUE (session_id, question_index);

ALTER TABLE ONLY public.interview_sessions
    ADD CONSTRAINT interview_sessions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.job_sources
    ADD CONSTRAINT job_sources_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.job_status_history
    ADD CONSTRAINT job_status_history_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT jobs_cloud_id_key UNIQUE (cloud_id);

ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT jobs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT jobs_public_id_key UNIQUE (public_id);

ALTER TABLE ONLY public.llm_cache
    ADD CONSTRAINT llm_cache_pkey PRIMARY KEY (cache_key);

ALTER TABLE ONLY public.profile_awards
    ADD CONSTRAINT profile_awards_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.profile_awards
    ADD CONSTRAINT profile_awards_public_id_key UNIQUE (public_id);

ALTER TABLE ONLY public.profile_certifications
    ADD CONSTRAINT profile_certifications_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.profile_certifications
    ADD CONSTRAINT profile_certifications_public_id_key UNIQUE (public_id);

ALTER TABLE ONLY public.profile_education
    ADD CONSTRAINT profile_education_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.profile_education
    ADD CONSTRAINT profile_education_public_id_key UNIQUE (public_id);

ALTER TABLE ONLY public.profile_imports
    ADD CONSTRAINT profile_imports_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.profile_imports
    ADD CONSTRAINT profile_imports_public_id_key UNIQUE (public_id);

ALTER TABLE ONLY public.profile_languages
    ADD CONSTRAINT profile_languages_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.profile_languages
    ADD CONSTRAINT profile_languages_public_id_key UNIQUE (public_id);

ALTER TABLE ONLY public.profile_projects
    ADD CONSTRAINT profile_projects_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.profile_projects
    ADD CONSTRAINT profile_projects_public_id_key UNIQUE (public_id);

ALTER TABLE ONLY public.profile_skills
    ADD CONSTRAINT profile_skills_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.profile_skills
    ADD CONSTRAINT profile_skills_public_id_key UNIQUE (public_id);

ALTER TABLE ONLY public.profile_work_experience
    ADD CONSTRAINT profile_work_experience_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.profile_work_experience
    ADD CONSTRAINT profile_work_experience_public_id_key UNIQUE (public_id);

ALTER TABLE ONLY public.public_job_sync
    ADD CONSTRAINT public_job_sync_pkey PRIMARY KEY (singleton);

ALTER TABLE ONLY public.recommendation_chunk_embeddings
    ADD CONSTRAINT recommendation_chunk_embeddings_pkey PRIMARY KEY (chunk_id, provider, model, dimensions);

ALTER TABLE ONLY public.recommendation_chunks
    ADD CONSTRAINT recommendation_chunks_document_id_chunk_index_key UNIQUE (document_id, chunk_index);

ALTER TABLE ONLY public.recommendation_chunks
    ADD CONSTRAINT recommendation_chunks_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.recommendation_corpus_state
    ADD CONSTRAINT recommendation_corpus_state_pkey PRIMARY KEY (state_key);

ALTER TABLE ONLY public.recommendation_documents
    ADD CONSTRAINT recommendation_documents_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.recommendation_documents
    ADD CONSTRAINT recommendation_documents_source_source_job_id_key UNIQUE (source, source_job_id);

ALTER TABLE ONLY public.recommendation_index_queue
    ADD CONSTRAINT recommendation_index_queue_pkey PRIMARY KEY (public_job_id);

ALTER TABLE ONLY public.recruiting_term_generations
    ADD CONSTRAINT recruiting_term_generations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.resume_documents
    ADD CONSTRAINT resume_documents_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.resume_documents
    ADD CONSTRAINT resume_documents_profile_id_sha256_key UNIQUE (profile_id, sha256);

ALTER TABLE ONLY public.resume_documents
    ADD CONSTRAINT resume_documents_public_id_key UNIQUE (public_id);

ALTER TABLE ONLY public.user_profiles
    ADD CONSTRAINT user_profiles_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.user_profiles
    ADD CONSTRAINT user_profiles_profile_key_key UNIQUE (profile_key);

ALTER TABLE ONLY public.user_profiles
    ADD CONSTRAINT user_profiles_public_id_key UNIQUE (public_id);

CREATE INDEX idx_agent_approvals_session_pending ON public.agent_approvals USING btree (session_id, status) WHERE ((status)::text = 'pending'::text);

CREATE INDEX idx_agent_memories_status ON public.agent_memories USING btree (status, updated_at DESC);

CREATE INDEX idx_agent_messages_session ON public.agent_messages USING btree (session_id, created_at);

CREATE INDEX idx_agent_messages_session_token ON public.agent_messages USING btree (session_id, created_at, id);

CREATE INDEX idx_agent_tool_calls_session ON public.agent_tool_calls USING btree (session_id, created_at);

CREATE INDEX idx_dedupe_candidates_pending ON public.dedupe_candidates USING btree (score DESC, id) WHERE (status = 0);

CREATE INDEX idx_dedupe_decisions_result_time ON public.dedupe_decisions USING btree (resulting_job_id, created_at DESC);

CREATE INDEX idx_dedupe_decisions_source_time ON public.dedupe_decisions USING btree (source_fingerprint, created_at DESC);

CREATE INDEX idx_ingestion_runs_started ON public.ingestion_runs USING btree (started_at DESC, id DESC);

CREATE INDEX idx_interview_answers_session ON public.interview_answers USING btree (session_id, question_index);

CREATE INDEX idx_interview_sessions_created ON public.interview_sessions USING btree (created_at DESC);

CREATE INDEX idx_job_sources_direct_hash ON public.job_sources USING btree (direct_url_hash, job_id) WHERE (direct_url_hash IS NOT NULL);

CREATE INDEX idx_job_sources_job ON public.job_sources USING btree (job_id, id);

CREATE INDEX idx_job_sources_linkedin_detail_freshness ON public.job_sources USING btree (details_fetched_at DESC) WHERE (source = 'linkedin'::text);

CREATE INDEX idx_job_sources_source_job ON public.job_sources USING btree (source, job_id);

CREATE INDEX idx_job_status_history_job_time ON public.job_status_history USING btree (job_id, changed_at DESC, id DESC);

CREATE INDEX idx_jobs_active_annual_salary ON public.jobs USING btree (salary_currency, salary_annual_max, published_sort_at DESC, id DESC) WHERE ((status = 1) AND (salary_annual_max IS NOT NULL));

CREATE INDEX idx_jobs_active_category_feed ON public.jobs USING btree (job_category, published_sort_at DESC, id DESC) WHERE (status = 1);

CREATE INDEX idx_jobs_active_company_feed ON public.jobs USING btree (company_normalized, published_sort_at DESC, id DESC) WHERE (status = 1);

CREATE INDEX idx_jobs_active_country_region_feed ON public.jobs USING btree (country_code, region_code, published_sort_at DESC, id DESC) WHERE (status = 1);

CREATE INDEX idx_jobs_active_dedupe_block ON public.jobs USING btree (dedupe_block_key, date_posted DESC, id DESC) WHERE (status = 1);

CREATE INDEX idx_jobs_active_employment_feed ON public.jobs USING btree (primary_employment_type, published_sort_at DESC, id DESC) WHERE (status = 1);

CREATE INDEX idx_jobs_active_feed ON public.jobs USING btree (published_sort_at DESC, id DESC) WHERE (status = 1);

CREATE INDEX idx_jobs_active_location_hierarchy_feed ON public.jobs USING btree (country_code, region_code, city, published_sort_at DESC, id DESC) WHERE (status = 1);

CREATE INDEX idx_jobs_active_opportunity_feed ON public.jobs USING btree (opportunity_type, published_sort_at DESC, id DESC) WHERE (status = 1);

CREATE INDEX idx_jobs_active_posted ON public.jobs USING btree (date_posted DESC, id DESC) WHERE ((status = 1) AND (date_posted IS NOT NULL));

CREATE INDEX idx_jobs_active_recruiting_term ON public.jobs USING btree (recruiting_year, recruiting_season, published_sort_at DESC, id DESC) WHERE ((status = 1) AND (recruiting_season IS NOT NULL));

CREATE INDEX idx_jobs_active_salary ON public.jobs USING btree (salary_currency, salary_max, published_sort_at DESC, id DESC) WHERE ((status = 1) AND (salary_max IS NOT NULL));

CREATE INDEX idx_jobs_active_schedule_feed ON public.jobs USING btree (primary_schedule_type, published_sort_at DESC, id DESC) WHERE (status = 1);

CREATE INDEX idx_jobs_active_search ON public.jobs USING gin (search_document) WHERE (status = 1);

CREATE INDEX idx_jobs_active_skill_tags ON public.jobs USING gin (skill_tags) WHERE (status = 1);

CREATE INDEX idx_jobs_active_subcategories ON public.jobs USING gin (job_subcategories) WHERE (status = 1);

CREATE INDEX idx_jobs_active_work_mode_feed ON public.jobs USING btree (work_mode, published_sort_at DESC, id DESC) WHERE (status = 1);

CREATE INDEX idx_jobs_description_hash ON public.jobs USING btree (description_hash) WHERE (description_hash IS NOT NULL);

CREATE INDEX idx_llm_cache_created ON public.llm_cache USING btree (created_at);

CREATE INDEX idx_profile_awards_order ON public.profile_awards USING btree (profile_id, "position");

CREATE INDEX idx_profile_certifications_order ON public.profile_certifications USING btree (profile_id, "position");

CREATE INDEX idx_profile_education_order ON public.profile_education USING btree (profile_id, "position");

CREATE INDEX idx_profile_imports_resume ON public.profile_imports USING btree (resume_id, created_at DESC);

CREATE INDEX idx_profile_languages_order ON public.profile_languages USING btree (profile_id, "position");

CREATE INDEX idx_profile_projects_order ON public.profile_projects USING btree (profile_id, "position");

CREATE INDEX idx_profile_skills_order ON public.profile_skills USING btree (profile_id, "position");

CREATE INDEX idx_profile_work_order ON public.profile_work_experience USING btree (profile_id, "position");

CREATE INDEX idx_raw_snapshots_run ON ONLY public.raw_job_snapshots USING btree (ingestion_run_id, id);

CREATE INDEX idx_raw_snapshots_scraped_brin ON ONLY public.raw_job_snapshots USING brin (scraped_at) WITH (pages_per_range='128');

CREATE INDEX idx_raw_snapshots_source_time ON ONLY public.raw_job_snapshots USING btree (job_source_id, scraped_at DESC);

CREATE INDEX idx_recommendation_chunks_document ON public.recommendation_chunks USING btree (document_id, chunk_index);

CREATE INDEX idx_recommendation_documents_public_job ON public.recommendation_documents USING btree (public_job_id) WHERE (public_job_id IS NOT NULL);

CREATE INDEX idx_recommendation_documents_search ON public.recommendation_documents USING gin (search_document);

CREATE INDEX idx_recommendation_documents_skills ON public.recommendation_documents USING gin (normalized_skills);

CREATE INDEX idx_recommendation_documents_source_updated ON public.recommendation_documents USING btree (source, indexed_at DESC, source_job_id);

CREATE INDEX idx_recommendation_embeddings_profile ON public.recommendation_chunk_embeddings USING btree (provider, model, dimensions, chunk_id);

CREATE INDEX idx_recommendation_index_queue_order ON public.recommendation_index_queue USING btree (queued_at, public_job_id);

CREATE INDEX idx_recruiting_term_generations_job ON public.recruiting_term_generations USING btree (job_id, started_at DESC);

CREATE INDEX idx_resume_documents_created ON public.resume_documents USING btree (profile_id, created_at DESC);

CREATE INDEX idx_tracker_active_updated ON public.application_tracker USING btree (updated_at DESC) WHERE (archived_at IS NULL);

CREATE INDEX idx_tracker_applied_at ON public.application_tracker USING btree (applied_at DESC);

CREATE INDEX idx_tracker_deadline ON public.application_tracker USING btree (application_deadline) WHERE (archived_at IS NULL);

CREATE INDEX idx_tracker_events_application ON public.application_tracker_events USING btree (application_id, occurred_at DESC);

CREATE INDEX idx_tracker_external_stage ON public.application_tracker USING btree (source_type, external_stage) WHERE (external_stage IS NOT NULL);

CREATE INDEX idx_tracker_follow_up_at ON public.application_tracker USING btree (follow_up_at) WHERE (archived_at IS NULL);

CREATE INDEX idx_tracker_job_id ON public.application_tracker USING btree (job_id);

CREATE INDEX idx_tracker_stage ON public.application_tracker USING btree (stage);

CREATE INDEX idx_tracker_updated_at ON public.application_tracker USING btree (updated_at DESC);

CREATE INDEX raw_job_snapshots_default_ingestion_run_id_id_idx ON public.raw_job_snapshots_default USING btree (ingestion_run_id, id);

CREATE INDEX raw_job_snapshots_default_job_source_id_scraped_at_idx ON public.raw_job_snapshots_default USING btree (job_source_id, scraped_at DESC);

CREATE INDEX raw_job_snapshots_default_scraped_at_idx ON public.raw_job_snapshots_default USING brin (scraped_at) WITH (pages_per_range='128');

CREATE UNIQUE INDEX uq_agent_memories_active_hash ON public.agent_memories USING btree (content_hash) WHERE ((status)::text = 'ACTIVE'::text);

CREATE UNIQUE INDEX uq_dedupe_candidates_pair ON public.dedupe_candidates USING btree (incoming_job_id, candidate_job_id);

CREATE UNIQUE INDEX uq_job_sources_fingerprint ON public.job_sources USING btree (source_fingerprint);

CREATE UNIQUE INDEX uq_job_sources_source_job_id ON public.job_sources USING btree (source, source_job_id) WHERE (source_job_id IS NOT NULL);

CREATE UNIQUE INDEX uq_tracker_external_source ON public.application_tracker USING btree (source_type, external_job_id) WHERE (external_job_id IS NOT NULL);

CREATE UNIQUE INDEX uq_tracker_platform_job ON public.application_tracker USING btree (job_id) WHERE (job_id IS NOT NULL);

ALTER INDEX public.idx_raw_snapshots_run ATTACH PARTITION public.raw_job_snapshots_default_ingestion_run_id_id_idx;

ALTER INDEX public.idx_raw_snapshots_source_time ATTACH PARTITION public.raw_job_snapshots_default_job_source_id_scraped_at_idx;

ALTER INDEX public.idx_raw_snapshots_scraped_brin ATTACH PARTITION public.raw_job_snapshots_default_scraped_at_idx;

CREATE TRIGGER trg_jobs_recommendation_queue AFTER INSERT OR UPDATE OF title, company_name, location_text, work_mode, opportunity_type, job_category, job_function, skill_tags, requirement_tags, date_posted, description, status ON public.jobs FOR EACH ROW EXECUTE FUNCTION public.enqueue_recommendation_document();

ALTER TABLE ONLY public.agent_approvals
    ADD CONSTRAINT agent_approvals_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.agent_sessions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.agent_audit_log
    ADD CONSTRAINT agent_audit_log_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.agent_sessions(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.agent_conversation_summaries
    ADD CONSTRAINT agent_conversation_summaries_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.agent_sessions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.agent_memories
    ADD CONSTRAINT agent_memories_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.agent_sessions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.agent_messages
    ADD CONSTRAINT agent_messages_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.agent_sessions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.agent_tool_calls
    ADD CONSTRAINT agent_tool_calls_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.agent_messages(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.agent_tool_calls
    ADD CONSTRAINT agent_tool_calls_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.agent_sessions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.application_tracker_events
    ADD CONSTRAINT application_tracker_events_application_id_fkey FOREIGN KEY (application_id) REFERENCES public.application_tracker(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.application_tracker
    ADD CONSTRAINT application_tracker_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.jobs(public_id) ON DELETE SET NULL;

ALTER TABLE ONLY public.dedupe_candidates
    ADD CONSTRAINT dedupe_candidates_candidate_job_id_fkey FOREIGN KEY (candidate_job_id) REFERENCES public.jobs(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.dedupe_candidates
    ADD CONSTRAINT dedupe_candidates_incoming_job_id_fkey FOREIGN KEY (incoming_job_id) REFERENCES public.jobs(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.dedupe_decisions
    ADD CONSTRAINT dedupe_decisions_compared_job_id_fkey FOREIGN KEY (compared_job_id) REFERENCES public.jobs(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.dedupe_decisions
    ADD CONSTRAINT dedupe_decisions_resulting_job_id_fkey FOREIGN KEY (resulting_job_id) REFERENCES public.jobs(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.interview_answers
    ADD CONSTRAINT interview_answers_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.interview_sessions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.job_sources
    ADD CONSTRAINT job_sources_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.jobs(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.job_status_history
    ADD CONSTRAINT job_status_history_ingestion_run_id_fkey FOREIGN KEY (ingestion_run_id) REFERENCES public.ingestion_runs(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.job_status_history
    ADD CONSTRAINT job_status_history_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.jobs(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.profile_awards
    ADD CONSTRAINT profile_awards_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES public.user_profiles(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.profile_certifications
    ADD CONSTRAINT profile_certifications_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES public.user_profiles(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.profile_education
    ADD CONSTRAINT profile_education_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES public.user_profiles(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.profile_imports
    ADD CONSTRAINT profile_imports_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES public.user_profiles(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.profile_imports
    ADD CONSTRAINT profile_imports_resume_id_fkey FOREIGN KEY (resume_id) REFERENCES public.resume_documents(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.profile_languages
    ADD CONSTRAINT profile_languages_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES public.user_profiles(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.profile_projects
    ADD CONSTRAINT profile_projects_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES public.user_profiles(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.profile_skills
    ADD CONSTRAINT profile_skills_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES public.user_profiles(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.profile_work_experience
    ADD CONSTRAINT profile_work_experience_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES public.user_profiles(id) ON DELETE CASCADE;

ALTER TABLE public.raw_job_snapshots
    ADD CONSTRAINT raw_job_snapshots_ingestion_run_id_fkey FOREIGN KEY (ingestion_run_id) REFERENCES public.ingestion_runs(id) ON DELETE RESTRICT;

ALTER TABLE public.raw_job_snapshots
    ADD CONSTRAINT raw_job_snapshots_job_source_id_fkey FOREIGN KEY (job_source_id) REFERENCES public.job_sources(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.recommendation_chunk_embeddings
    ADD CONSTRAINT recommendation_chunk_embeddings_chunk_id_fkey FOREIGN KEY (chunk_id) REFERENCES public.recommendation_chunks(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.recommendation_chunks
    ADD CONSTRAINT recommendation_chunks_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.recommendation_documents(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.recommendation_documents
    ADD CONSTRAINT recommendation_documents_public_job_id_fkey FOREIGN KEY (public_job_id) REFERENCES public.jobs(public_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.recommendation_index_queue
    ADD CONSTRAINT recommendation_index_queue_public_job_id_fkey FOREIGN KEY (public_job_id) REFERENCES public.jobs(public_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.recruiting_term_generations
    ADD CONSTRAINT recruiting_term_generations_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.jobs(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.resume_documents
    ADD CONSTRAINT resume_documents_profile_id_fkey FOREIGN KEY (profile_id) REFERENCES public.user_profiles(id) ON DELETE CASCADE;

INSERT INTO public.user_profiles (profile_key) VALUES ('default');
INSERT INTO public.recommendation_corpus_state (state_key) VALUES ('default');
INSERT INTO public.public_job_sync (version) VALUES (0);
