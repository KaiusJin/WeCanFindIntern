BEGIN;

CREATE INDEX IF NOT EXISTS idx_recommendation_documents_source_updated
    ON recommendation_documents (source,indexed_at DESC,source_job_id);
CREATE INDEX IF NOT EXISTS idx_recommendation_chunks_model
    ON recommendation_chunks (embedding_model)
    WHERE embedding IS NOT NULL;

COMMIT;
