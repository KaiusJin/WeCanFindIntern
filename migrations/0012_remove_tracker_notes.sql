BEGIN;

-- Note functionality is intentionally removed. This deletes historical note
-- events before removing the legacy free-form notes column.
DELETE FROM application_tracker_events
WHERE event_type = 'note';

ALTER TABLE application_tracker
    DROP COLUMN IF EXISTS notes;

ALTER TABLE application_tracker_events
    ALTER COLUMN event_type DROP DEFAULT,
    DROP CONSTRAINT IF EXISTS application_tracker_event_type_no_note,
    ADD CONSTRAINT application_tracker_event_type_no_note
        CHECK (event_type <> 'note');

COMMIT;
