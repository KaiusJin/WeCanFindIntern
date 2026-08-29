BEGIN;

-- ---------------------------------------------------------------------------
-- AI Agent: conversation, tool-call, approval and audit persistence.
-- The MVP is single-user; session ownership is implied.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS agent_sessions (
    id BIGSERIAL PRIMARY KEY,
    public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    title VARCHAR(255) NOT NULL DEFAULT 'New conversation',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_messages (
    id BIGSERIAL PRIMARY KEY,
    public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    session_id BIGINT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    role VARCHAR(16) NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_messages_session
    ON agent_messages(session_id, created_at);

CREATE TABLE IF NOT EXISTS agent_tool_calls (
    id BIGSERIAL PRIMARY KEY,
    public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    session_id BIGINT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    message_id BIGINT REFERENCES agent_messages(id) ON DELETE CASCADE,
    tool_name VARCHAR(64) NOT NULL,
    arguments JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(24) NOT NULL DEFAULT 'succeeded'
        CHECK (status IN ('succeeded', 'failed', 'awaiting_approval')),
    result JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_tool_calls_session
    ON agent_tool_calls(session_id, created_at);

CREATE TABLE IF NOT EXISTS agent_approvals (
    id BIGSERIAL PRIMARY KEY,
    public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    session_id BIGINT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    tool_name VARCHAR(64) NOT NULL,
    arguments JSONB NOT NULL DEFAULT '{}'::jsonb,
    preview JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'denied')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_agent_approvals_session_pending
    ON agent_approvals(session_id, status)
    WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS agent_audit_log (
    id BIGSERIAL PRIMARY KEY,
    session_id BIGINT REFERENCES agent_sessions(id) ON DELETE SET NULL,
    user_intent TEXT,
    tool_name VARCHAR(64),
    arguments_summary TEXT,
    approval_status VARCHAR(16),
    result_summary TEXT,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- WaterlooWorks tracker source references.
-- A tracker record may reference either a public job (job_id) or an external
-- source (source_type + external_job_id); never both paths into public jobs.
-- ---------------------------------------------------------------------------

ALTER TABLE application_tracker
    ADD COLUMN IF NOT EXISTS external_job_id VARCHAR(255);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tracker_external_source
    ON application_tracker(source_type, external_job_id)
    WHERE external_job_id IS NOT NULL;

COMMIT;
