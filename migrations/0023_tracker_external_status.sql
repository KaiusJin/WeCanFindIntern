BEGIN;

ALTER TABLE application_tracker
    ADD COLUMN IF NOT EXISTS external_stage VARCHAR(32),
    ADD COLUMN IF NOT EXISTS external_status VARCHAR(128);

UPDATE application_tracker
SET external_stage = stage
WHERE source_type = 'waterloo_work'
  AND external_stage IS NULL;

ALTER TABLE application_tracker
    DROP CONSTRAINT IF EXISTS application_tracker_external_stage_chk,
    ADD CONSTRAINT application_tracker_external_stage_chk
        CHECK (
            external_stage IS NULL OR external_stage IN (
                'interested', 'applied', 'interview', 'offer', 'rejected'
            )
        );

CREATE INDEX IF NOT EXISTS idx_tracker_external_stage
    ON application_tracker(source_type, external_stage)
    WHERE external_stage IS NOT NULL;

COMMIT;
