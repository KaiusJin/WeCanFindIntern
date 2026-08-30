BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS recommendation_documents (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL CHECK (source IN ('public', 'waterloo_work')),
    source_job_id TEXT NOT NULL,
    public_job_id UUID REFERENCES jobs(public_id) ON DELETE CASCADE,
    content_hash CHAR(64) NOT NULL,
    title TEXT NOT NULL,
    role_family TEXT,
    normalized_skills TEXT[] NOT NULL DEFAULT '{}',
    required_skills TEXT[] NOT NULL DEFAULT '{}',
    preferred_skills TEXT[] NOT NULL DEFAULT '{}',
    document_text TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    index_version TEXT NOT NULL DEFAULT 'recommend-document.v1',
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    search_document TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('simple', coalesce(role_family, '')), 'B') ||
        setweight(to_tsvector('simple', coalesce(document_text, '')), 'C')
    ) STORED,
    CONSTRAINT recommendation_documents_identity_chk CHECK (
        (source = 'public' AND public_job_id IS NOT NULL AND source_job_id = public_job_id::text)
        OR (source = 'waterloo_work' AND public_job_id IS NULL)
    ),
    UNIQUE (source, source_job_id)
);

CREATE TABLE IF NOT EXISTS recommendation_chunks (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES recommendation_documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    chunk_type TEXT NOT NULL DEFAULT 'description',
    chunk_text TEXT NOT NULL,
    chunk_hash CHAR(64) NOT NULL,
    embedding VECTOR(1536),
    embedding_model TEXT,
    embedding_version TEXT,
    embedded_at TIMESTAMPTZ,
    UNIQUE (document_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS recommendation_corpus_state (
    state_key TEXT PRIMARY KEY,
    corpus_version BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO recommendation_corpus_state (state_key)
VALUES ('default')
ON CONFLICT (state_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS recommendation_index_queue (
    public_job_id UUID PRIMARY KEY REFERENCES jobs(public_id) ON DELETE CASCADE,
    queued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);

CREATE OR REPLACE FUNCTION enqueue_recommendation_document()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = 1 THEN
        INSERT INTO recommendation_index_queue (public_job_id,queued_at,attempts,last_error)
        VALUES (NEW.public_id,now(),0,NULL)
        ON CONFLICT (public_job_id) DO UPDATE SET
            queued_at=EXCLUDED.queued_at,attempts=0,last_error=NULL;
    ELSE
        DELETE FROM recommendation_index_queue WHERE public_job_id=NEW.public_id;
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_jobs_recommendation_queue ON jobs;
CREATE TRIGGER trg_jobs_recommendation_queue
AFTER INSERT OR UPDATE OF title,company_name,location_text,work_mode,
    opportunity_type,job_category,job_function,skill_tags,requirement_tags,
    date_posted,description,status
ON jobs FOR EACH ROW EXECUTE FUNCTION enqueue_recommendation_document();

INSERT INTO recommendation_index_queue (public_job_id)
SELECT public_id FROM jobs WHERE status=1
ON CONFLICT (public_job_id) DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_recommendation_documents_search
    ON recommendation_documents USING GIN (search_document);
CREATE INDEX IF NOT EXISTS idx_recommendation_documents_skills
    ON recommendation_documents USING GIN (normalized_skills);
CREATE INDEX IF NOT EXISTS idx_recommendation_documents_public_job
    ON recommendation_documents (public_job_id)
    WHERE public_job_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_recommendation_chunks_document
    ON recommendation_chunks (document_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_recommendation_chunks_embedding_hnsw
    ON recommendation_chunks USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_recommendation_index_queue_order
    ON recommendation_index_queue (queued_at,public_job_id);

COMMIT;
