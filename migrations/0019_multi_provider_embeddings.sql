BEGIN;

CREATE TABLE IF NOT EXISTS recommendation_chunk_embeddings (
    chunk_id BIGINT NOT NULL REFERENCES recommendation_chunks(id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider IN ('OpenAI', 'Gemini', 'Ollama')),
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK (dimensions BETWEEN 1 AND 4096),
    embedding VECTOR NOT NULL,
    embedding_version TEXT NOT NULL,
    embedded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (chunk_id, provider, model, dimensions)
);

INSERT INTO recommendation_chunk_embeddings (
    chunk_id,provider,model,dimensions,embedding,embedding_version,embedded_at
)
SELECT id,'OpenAI',embedding_model,1536,embedding,embedding_version,
       coalesce(embedded_at,now())
FROM recommendation_chunks
WHERE embedding IS NOT NULL AND embedding_model IS NOT NULL
ON CONFLICT (chunk_id,provider,model,dimensions) DO NOTHING;

DROP INDEX IF EXISTS idx_recommendation_chunks_embedding_hnsw;
DROP INDEX IF EXISTS idx_recommendation_chunks_model;

ALTER TABLE recommendation_chunks
    DROP COLUMN IF EXISTS embedding,
    DROP COLUMN IF EXISTS embedding_model,
    DROP COLUMN IF EXISTS embedding_version,
    DROP COLUMN IF EXISTS embedded_at;

CREATE INDEX IF NOT EXISTS idx_recommendation_embeddings_profile
    ON recommendation_chunk_embeddings (provider,model,dimensions,chunk_id);

COMMIT;
