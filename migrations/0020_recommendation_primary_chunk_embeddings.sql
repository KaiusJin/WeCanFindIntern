BEGIN;

-- Recommendation recall uses one representative vector per job. Full JD text
-- remains covered by the generated tsvector index, so embedding every overlap
-- chunk adds substantial inference/storage cost without improving first-stage
-- candidate breadth proportionally.
DELETE FROM recommendation_chunk_embeddings e
USING recommendation_chunks c
WHERE e.chunk_id=c.id AND c.chunk_index<>0;

COMMIT;
