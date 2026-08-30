BEGIN;

-- Content-addressed cache for LLM responses. The key is the SHA-256 of the
-- full request (provider + model + system prompt + user prompt), so identical
-- inputs skip the provider entirely. Entries expire via created_at + ttl.

CREATE TABLE IF NOT EXISTS llm_cache (
    cache_key   CHAR(64) PRIMARY KEY,
    provider    TEXT NOT NULL,
    model       TEXT NOT NULL,
    response    TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_llm_cache_created
    ON llm_cache (created_at);

COMMENT ON TABLE llm_cache IS
    'Content-addressed LLM response cache; identical prompts return instantly';

COMMIT;
