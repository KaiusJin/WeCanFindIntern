BEGIN;

-- ---------------------------------------------------------------------------
-- AI Agent memory: rolling summaries, long-term typed memories, explicit
-- user preferences, and per-message token accounting. Design follows the
-- NoteFlow conversation-memory model, adapted to the single-user MVP.
-- ---------------------------------------------------------------------------

ALTER TABLE agent_messages
    ADD COLUMN IF NOT EXISTS token_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE agent_sessions
    ADD COLUMN IF NOT EXISTS summary_text TEXT,
    ADD COLUMN IF NOT EXISTS summary_json JSONB,
    ADD COLUMN IF NOT EXISTS summary_version INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS summary_token_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS summary_covers_through_message_id UUID,
    ADD COLUMN IF NOT EXISTS extraction_covers_through_message_id UUID,
    ADD COLUMN IF NOT EXISTS last_message_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_agent_messages_session_token
    ON agent_messages(session_id, created_at, id);

CREATE TABLE IF NOT EXISTS agent_conversation_summaries (
    id BIGSERIAL PRIMARY KEY,
    session_id BIGINT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    summary_text TEXT NOT NULL,
    summary_json JSONB NOT NULL,
    token_count INTEGER NOT NULL DEFAULT 0,
    covered_message_count INTEGER NOT NULL DEFAULT 0,
    covers_through_message_id UUID,
    provider VARCHAR(32),
    model VARCHAR(128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, version)
);

CREATE TABLE IF NOT EXISTS agent_memories (
    id BIGSERIAL PRIMARY KEY,
    public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    session_id BIGINT REFERENCES agent_sessions(id) ON DELETE CASCADE,
    memory_type VARCHAR(32) NOT NULL
        CHECK (memory_type IN ('USER_PREFERENCE', 'CAREER_CONTEXT', 'JOB_TARGET', 'EXPLICIT_FACT')),
    content TEXT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    confidence NUMERIC(4,3) NOT NULL DEFAULT 1.0,
    status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'SUPERSEDED', 'EXPIRED')),
    source_message_id UUID,
    superseded_by UUID,
    access_count INTEGER NOT NULL DEFAULT 0,
    last_accessed_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_memories_status
    ON agent_memories(status, updated_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_memories_active_hash
    ON agent_memories(content_hash)
    WHERE status = 'ACTIVE';

CREATE TABLE IF NOT EXISTS agent_user_preferences (
    id BIGSERIAL PRIMARY KEY,
    preference_key VARCHAR(64) NOT NULL UNIQUE,
    preference_value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
