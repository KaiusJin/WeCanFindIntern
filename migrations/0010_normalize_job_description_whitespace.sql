WITH matching_snapshots AS (
    SELECT
        source.job_id,
        snapshot.payload->>'description' AS raw_description,
        row_number() OVER (
            PARTITION BY source.job_id
            ORDER BY
                length(snapshot.payload->>'description') DESC,
                snapshot.scraped_at DESC,
                snapshot.id DESC
        ) AS preference
    FROM job_sources source
    JOIN raw_job_snapshots snapshot
        ON snapshot.job_source_id = source.id
    JOIN jobs job
        ON job.id = source.job_id
    WHERE NULLIF(snapshot.payload->>'description', '') IS NOT NULL
      AND btrim(regexp_replace(
            snapshot.payload->>'description',
            '[[:space:]]+',
            ' ',
            'g'
          )) = btrim(regexp_replace(
            job.description,
            '[[:space:]]+',
            ' ',
            'g'
          ))
), normalized_descriptions AS (
    SELECT
        job_id,
        btrim(
            regexp_replace(
                regexp_replace(
                    regexp_replace(
                        replace(
                            replace(raw_description, E'\r\n', E'\n'),
                            E'\r',
                            E'\n'
                        ),
                        '[[:blank:]]+',
                        ' ',
                        'g'
                    ),
                    E' *\n *',
                    E'\n',
                    'g'
                ),
                E'\n+',
                E'\n',
                'g'
            ),
            E' \n\t\r'
        ) AS description
    FROM matching_snapshots
    WHERE preference = 1
)
UPDATE jobs job
SET description = normalized.description,
    description_hash = digest(normalized.description, 'sha256'),
    updated_at = now()
FROM normalized_descriptions normalized
WHERE job.id = normalized.job_id
  AND job.description IS DISTINCT FROM normalized.description;

UPDATE application_tracker tracker
SET job_description = job.description,
    updated_at = now()
FROM jobs job
WHERE tracker.job_id = job.public_id
  AND tracker.origin_type = 'platform_bookmark'
  AND tracker.job_description IS DISTINCT FROM job.description;
