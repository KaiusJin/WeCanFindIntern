BEGIN;

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

COMMIT;
