CREATE TABLE IF NOT EXISTS application_tracker (
    id BIGSERIAL PRIMARY KEY,
    public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    job_id UUID REFERENCES jobs(public_id) ON DELETE SET NULL,
    company_name VARCHAR(255) NOT NULL,
    title VARCHAR(255) NOT NULL,
    location_text VARCHAR(255),
    work_mode VARCHAR(64),
    job_url TEXT,
    job_description TEXT,
    salary_text VARCHAR(128),
    origin_type VARCHAR(32) NOT NULL DEFAULT 'custom',
    source_type VARCHAR(32) NOT NULL DEFAULT 'other',
    stage VARCHAR(32) NOT NULL DEFAULT 'interested',
    notes TEXT,
    applied_at TIMESTAMPTZ,
    interview_at TIMESTAMPTZ,
    offer_at TIMESTAMPTZ,
    rejected_at TIMESTAMPTZ,
    application_deadline DATE,
    follow_up_at TIMESTAMPTZ,
    source_text VARCHAR(128),
    priority VARCHAR(16) NOT NULL DEFAULT 'normal',
    next_step TEXT,
    archived_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Keep this migration safe for databases originally created by an older
-- version of 0009 before the scalable tracker workspace was introduced.
ALTER TABLE application_tracker
    ADD COLUMN IF NOT EXISTS application_deadline DATE,
    ADD COLUMN IF NOT EXISTS follow_up_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS source_text VARCHAR(128),
    ADD COLUMN IF NOT EXISTS priority VARCHAR(16) NOT NULL DEFAULT 'normal',
    ADD COLUMN IF NOT EXISTS next_step TEXT,
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS origin_type VARCHAR(32) NOT NULL DEFAULT 'custom',
    ADD COLUMN IF NOT EXISTS source_type VARCHAR(32) NOT NULL DEFAULT 'other';

ALTER TABLE application_tracker
    ADD COLUMN IF NOT EXISTS job_description TEXT;

UPDATE application_tracker
SET origin_type = 'platform_bookmark'
WHERE job_id IS NOT NULL;

UPDATE application_tracker a
SET company_name = COALESCE(NULLIF(j.company_name, ''), a.company_name),
    title = j.title,
    location_text = j.location_text,
    work_mode = j.work_mode,
    job_url = COALESCE(js.direct_url, js.source_url, a.job_url),
    job_description = j.description,
    source_type = CASE
    WHEN lower(COALESCE(js.source, '')) LIKE '%linkedin%' THEN 'linkedin'
    WHEN lower(COALESCE(js.source, '')) LIKE '%indeed%' THEN 'indeed'
    WHEN lower(COALESCE(js.source, '')) LIKE '%waterloo%' THEN 'waterloo_work'
    WHEN lower(COALESCE(a.source_text, '')) LIKE '%linkedin%' THEN 'linkedin'
    WHEN lower(COALESCE(a.source_text, '')) LIKE '%indeed%' THEN 'indeed'
    WHEN lower(COALESCE(a.source_text, '')) LIKE '%waterloo%' THEN 'waterloo_work'
    ELSE 'wecanfindintern'
END
FROM jobs j
LEFT JOIN LATERAL (
    SELECT source, source_url, direct_url
    FROM job_sources
    WHERE job_id = j.id
    ORDER BY first_seen_at, id
    LIMIT 1
) js ON true
WHERE j.public_id = a.job_id;

UPDATE application_tracker
SET source_type = CASE
    WHEN lower(COALESCE(source_text, '')) LIKE '%linkedin%' THEN 'linkedin'
    WHEN lower(COALESCE(source_text, '')) LIKE '%indeed%' THEN 'indeed'
    WHEN lower(COALESCE(source_text, '')) LIKE '%waterloo%' THEN 'waterloo_work'
    ELSE 'other'
END
WHERE job_id IS NULL;

ALTER TABLE application_tracker
    DROP CONSTRAINT IF EXISTS application_tracker_origin_type_chk,
    ADD CONSTRAINT application_tracker_origin_type_chk
        CHECK (origin_type IN ('platform_bookmark', 'custom')),
    DROP CONSTRAINT IF EXISTS application_tracker_source_type_chk,
    ADD CONSTRAINT application_tracker_source_type_chk
        CHECK (source_type IN ('wecanfindintern', 'linkedin', 'indeed', 'waterloo_work', 'other'));

CREATE INDEX IF NOT EXISTS idx_tracker_stage ON application_tracker(stage);
CREATE INDEX IF NOT EXISTS idx_tracker_job_id ON application_tracker(job_id);
CREATE INDEX IF NOT EXISTS idx_tracker_updated_at ON application_tracker(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_tracker_applied_at
    ON application_tracker(applied_at DESC);
CREATE INDEX IF NOT EXISTS idx_tracker_follow_up_at
    ON application_tracker(follow_up_at)
    WHERE archived_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tracker_deadline
    ON application_tracker(application_deadline)
    WHERE archived_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tracker_active_updated
    ON application_tracker(updated_at DESC)
    WHERE archived_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_tracker_platform_job
    ON application_tracker(job_id)
    WHERE job_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS application_tracker_events (
    id BIGSERIAL PRIMARY KEY,
    public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    application_id BIGINT NOT NULL REFERENCES application_tracker(id) ON DELETE CASCADE,
    event_type VARCHAR(32) NOT NULL DEFAULT 'note',
    title VARCHAR(255) NOT NULL,
    details TEXT,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tracker_events_application
    ON application_tracker_events(application_id, occurred_at DESC);

INSERT INTO application_tracker_events (
    application_id, event_type, title, occurred_at, created_at
)
SELECT a.id, 'created', 'Added to tracker', a.created_at, a.created_at
FROM application_tracker a
WHERE NOT EXISTS (
    SELECT 1
    FROM application_tracker_events e
    WHERE e.application_id = a.id AND e.event_type = 'created'
);
