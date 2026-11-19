-- Applied inside the collector's private schema after the public ingestion migrations.
DROP TABLE IF EXISTS collection_checkpoints;
DROP TABLE IF EXISTS collection_plans CASCADE;
ALTER TABLE ingestion_runs DROP COLUMN IF EXISTS collection_plan_id;

CREATE TABLE feed_checkpoint (
    plan text NOT NULL,
    source text NOT NULL,
    campaign text NOT NULL,
    saved_at timestamptz NOT NULL DEFAULT now(),
    records bytea NOT NULL,
    PRIMARY KEY (plan, source)
);

-- Retain only the latest content hash/sequence per job. NULL is a pending tombstone.
CREATE SEQUENCE feed_release_seq;
CREATE TABLE feed_versions (
    public_id uuid PRIMARY KEY,
    content_hash bytea,
    sync_seq bigint NOT NULL
);
CREATE INDEX feed_versions_seq ON feed_versions(sync_seq);
