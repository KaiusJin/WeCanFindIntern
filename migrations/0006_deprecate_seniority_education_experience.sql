BEGIN;

COMMENT ON COLUMN jobs.seniority IS
    'Deprecated: retained only for historical compatibility; not written or exposed';
COMMENT ON COLUMN jobs.seniority_level IS
    'Deprecated: retained only for historical compatibility; not written or exposed';
COMMENT ON COLUMN jobs.education_levels IS
    'Deprecated: retained only for historical compatibility; not written or exposed';
COMMENT ON COLUMN jobs.experience_min_years IS
    'Deprecated: retained only for historical compatibility; not written or exposed';
COMMENT ON COLUMN jobs.experience_max_years IS
    'Deprecated: retained only for historical compatibility; not written or exposed';
COMMENT ON COLUMN jobs.experience_range IS
    'Deprecated: retained only for historical compatibility; not written or exposed';

COMMIT;
