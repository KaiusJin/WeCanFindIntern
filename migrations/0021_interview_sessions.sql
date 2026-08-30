BEGIN;

-- Persisted mock-interview practice: one row per practice run against a job
-- description, with the generated question set and one analyzed answer per
-- question. Answers upsert on (session_id, question_index) so re-analyzing a
-- question replaces its previous report.

CREATE TABLE IF NOT EXISTS interview_sessions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_description     TEXT NOT NULL,
    provider            TEXT NOT NULL DEFAULT '',
    model_name          TEXT NOT NULL DEFAULT '',
    questions           JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS interview_answers (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          UUID NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
    question_index      INTEGER NOT NULL,
    question_text       TEXT NOT NULL DEFAULT '',
    answer_text         TEXT NOT NULL DEFAULT '',
    transcript          TEXT NOT NULL DEFAULT '',
    transcript_language TEXT NOT NULL DEFAULT '',
    duration_seconds    DOUBLE PRECISION NOT NULL DEFAULT 0,
    score               INTEGER NOT NULL DEFAULT 0 CHECK (score BETWEEN 0 AND 100),
    summary             TEXT NOT NULL DEFAULT '',
    star_feedback       TEXT NOT NULL DEFAULT '',
    timeline            JSONB NOT NULL DEFAULT '[]'::jsonb,
    advice              JSONB NOT NULL DEFAULT '[]'::jsonb,
    provider            TEXT NOT NULL DEFAULT '',
    model_name          TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT interview_answers_session_question_key
        UNIQUE (session_id, question_index)
);

CREATE INDEX IF NOT EXISTS idx_interview_sessions_created
    ON interview_sessions (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_interview_answers_session
    ON interview_answers (session_id, question_index);

COMMENT ON TABLE interview_sessions IS
    'Mock-interview practice sessions: generated question set plus analyzed answers';
COMMENT ON TABLE interview_answers IS
    'Analyzed answer per question; upserted when a question is re-analyzed';

COMMIT;
