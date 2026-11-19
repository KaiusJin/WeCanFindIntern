BEGIN;

-- Pre-cloud bookmarks reference the device's public_id, which must remain stable.
ALTER TABLE jobs ADD COLUMN cloud_id uuid UNIQUE;
CREATE TABLE public_job_sync (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    version bigint NOT NULL CHECK (version >= 0)
);
INSERT INTO public_job_sync (version) VALUES (0);

COMMIT;
