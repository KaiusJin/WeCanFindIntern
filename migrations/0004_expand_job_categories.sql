BEGIN;

ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_job_category_chk;

ALTER TABLE jobs ADD CONSTRAINT jobs_job_category_chk CHECK (
    job_category IN (
        'software_engineering', 'data_ai', 'cybersecurity', 'cloud_devops',
        'qa_testing', 'product_design', 'product_management', 'it_support',
        'hardware_embedded', 'research', 'business_operations', 'finance',
        'marketing_sales', 'human_resources', 'healthcare', 'education',
        'skilled_trades', 'engineering', 'architecture_planning', 'legal',
        'customer_service', 'supply_chain', 'administrative', 'other'
    )
);

COMMIT;
