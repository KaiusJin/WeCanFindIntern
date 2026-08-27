BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'jobs_salary_interval_amount_chk'
    ) THEN
        ALTER TABLE jobs ADD CONSTRAINT jobs_salary_interval_amount_chk CHECK (
            salary_interval IS NULL
            OR (salary_interval = 'hourly'
                AND (salary_min IS NULL OR salary_min BETWEEN 5 AND 500)
                AND (salary_max IS NULL OR salary_max BETWEEN 5 AND 500))
            OR (salary_interval = 'daily'
                AND (salary_min IS NULL OR salary_min BETWEEN 40 AND 5000)
                AND (salary_max IS NULL OR salary_max BETWEEN 40 AND 5000))
            OR (salary_interval = 'weekly'
                AND (salary_min IS NULL OR salary_min BETWEEN 100 AND 25000)
                AND (salary_max IS NULL OR salary_max BETWEEN 100 AND 25000))
            OR (salary_interval = 'monthly'
                AND (salary_min IS NULL OR salary_min BETWEEN 500 AND 100000)
                AND (salary_max IS NULL OR salary_max BETWEEN 500 AND 100000))
            OR (salary_interval = 'yearly'
                AND (salary_min IS NULL OR salary_min BETWEEN 5000 AND 2000000)
                AND (salary_max IS NULL OR salary_max BETWEEN 5000 AND 2000000))
        );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'jobs_salary_annual_amount_chk'
    ) THEN
        ALTER TABLE jobs ADD CONSTRAINT jobs_salary_annual_amount_chk CHECK (
            (salary_annual_min IS NULL OR salary_annual_min BETWEEN 0 AND 2000000)
            AND (salary_annual_max IS NULL OR salary_annual_max BETWEEN 0 AND 2000000)
        );
    END IF;
END $$;

COMMENT ON CONSTRAINT jobs_salary_interval_amount_chk ON jobs IS
    'Rejects interval/value mismatches before they can reach the public API';

COMMIT;
