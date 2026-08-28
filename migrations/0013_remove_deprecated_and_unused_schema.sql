BEGIN;

-- Deprecated classification/seniority/experience columns (0001/0003/0006) are never
-- written or exposed by the API. Their CHECK constraints drop with the columns.
DROP INDEX IF EXISTS idx_jobs_active_seniority_feed;
ALTER TABLE jobs
    DROP COLUMN IF EXISTS seniority,
    DROP COLUMN IF EXISTS seniority_level,
    DROP COLUMN IF EXISTS education_levels,
    DROP COLUMN IF EXISTS experience_min_years,
    DROP COLUMN IF EXISTS experience_max_years,
    DROP COLUMN IF EXISTS experience_range;

-- Unused profile columns and a dead JSON index on a key that is never written.
ALTER TABLE user_profiles
    DROP COLUMN IF EXISTS headline,
    DROP COLUMN IF EXISTS summary;
DROP INDEX IF EXISTS idx_profile_skills_name;

-- The durable collection scheduler was removed; its tables and the FK column on
-- ingestion_runs are no longer used by any code path.
DROP TABLE IF EXISTS collection_checkpoints;
DROP TABLE IF EXISTS collection_plans CASCADE;
ALTER TABLE ingestion_runs DROP COLUMN IF EXISTS collection_plan_id;

COMMIT;
