CREATE TABLE IF NOT EXISTS application_tracker (
    id BIGSERIAL PRIMARY KEY,
    public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    job_id UUID REFERENCES jobs(public_id) ON DELETE SET NULL,
    company_name VARCHAR(255) NOT NULL,
    title VARCHAR(255) NOT NULL,
    location_text VARCHAR(255),
    work_mode VARCHAR(64),
    job_url TEXT,
    salary_text VARCHAR(128),
    stage VARCHAR(32) NOT NULL DEFAULT 'interested',
    notes TEXT,
    applied_at TIMESTAMPTZ,
    interview_at TIMESTAMPTZ,
    offer_at TIMESTAMPTZ,
    rejected_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tracker_stage ON application_tracker(stage);
CREATE INDEX IF NOT EXISTS idx_tracker_job_id ON application_tracker(job_id);
CREATE INDEX IF NOT EXISTS idx_tracker_updated_at ON application_tracker(updated_at DESC);
