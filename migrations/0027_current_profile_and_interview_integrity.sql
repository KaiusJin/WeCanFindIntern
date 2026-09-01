BEGIN;

-- Profile is a current-state document. Historical snapshots are intentionally
-- not retained; stable UUIDs on section rows identify the current entries.
DROP TABLE IF EXISTS profile_versions;

-- A persisted answer can only address a non-negative question position.
DELETE FROM interview_answers WHERE question_index < 0;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'interview_answers_question_index_nonnegative'
          AND conrelid = 'interview_answers'::regclass
    ) THEN
        ALTER TABLE interview_answers
            ADD CONSTRAINT interview_answers_question_index_nonnegative
            CHECK (question_index >= 0);
    END IF;
END
$$;

COMMIT;
