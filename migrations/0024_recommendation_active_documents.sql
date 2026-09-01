BEGIN;

-- Public recommendation documents are derived data and must not outlive the
-- active job that produced them. WaterlooWorks documents are unaffected.
DELETE FROM recommendation_documents d
WHERE d.source = 'public'
  AND NOT EXISTS (
      SELECT 1
      FROM jobs j
      WHERE j.public_id = d.public_job_id AND j.status = 1
  );

CREATE OR REPLACE FUNCTION enqueue_recommendation_document()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = 1 THEN
        INSERT INTO recommendation_index_queue (public_job_id,queued_at,attempts,last_error)
        VALUES (NEW.public_id,now(),0,NULL)
        ON CONFLICT (public_job_id) DO UPDATE SET
            queued_at=EXCLUDED.queued_at,attempts=0,last_error=NULL;
    ELSE
        DELETE FROM recommendation_index_queue WHERE public_job_id=NEW.public_id;
        DELETE FROM recommendation_documents
        WHERE source='public' AND public_job_id=NEW.public_id;
    END IF;
    RETURN NEW;
END $$;

COMMIT;
