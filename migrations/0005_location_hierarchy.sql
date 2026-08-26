BEGIN;

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

COMMIT;
