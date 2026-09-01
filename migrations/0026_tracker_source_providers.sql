BEGIN;

ALTER TABLE application_tracker
    DROP CONSTRAINT IF EXISTS application_tracker_source_type_chk,
    ADD CONSTRAINT application_tracker_source_type_chk
        CHECK (source_type IN (
            'wecanfindintern', 'linkedin', 'indeed', 'glassdoor',
            'zip_recruiter', 'google', 'waterloo_work', 'other'
        ));

COMMIT;
