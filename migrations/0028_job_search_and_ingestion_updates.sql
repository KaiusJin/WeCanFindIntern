BEGIN;

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

COMMIT;
