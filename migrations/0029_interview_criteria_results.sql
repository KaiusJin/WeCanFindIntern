BEGIN;

ALTER TABLE interview_answers
    ADD COLUMN IF NOT EXISTS criteria_results JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN interview_answers.criteria_results IS
    'Per-criterion verdicts returned by the interview analysis service';

COMMIT;
