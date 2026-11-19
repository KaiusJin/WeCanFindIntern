"""Public feed v1. These allowlists also define the desktop import boundary."""

import gzip
import hashlib
import json

JOB_FIELDS = [
    "title",
    "title_normalized",
    "company_name",
    "company_normalized",
    "company_industry",
    "company_website_url",
    "company_logo_url",
    "location_text",
    "city",
    "region_code",
    "region_name",
    "region_type",
    "country_code",
    "country_name",
    "location_normalized",
    "work_mode",
    "employment_types",
    "primary_employment_type",
    "opportunity_type",
    "schedule_types",
    "primary_schedule_type",
    "job_category",
    "job_subcategories",
    "skill_tags",
    "requirement_tags",
    "display_tags",
    "classification_version",
    "date_posted",
    "published_sort_at",
    "job_function",
    "description",
    "description_hash",
    "salary_interval",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_source",
    "salary_annual_min",
    "salary_annual_max",
    "source_skills",
    "contact_emails",
    "vacancy_count",
    "dedupe_block_key",
    "recruiting_season",
    "recruiting_year",
    "status",
    "closed_at",
    "first_seen_at",
]
SOURCE_FIELDS = [
    "source",
    "source_job_id",
    "source_url",
    "canonical_source_url",
    "direct_url",
    "canonical_direct_url",
    "direct_url_hash",
    "source_fingerprint",
    "first_seen_at",
]


def encode_package(rows, folder, version):
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    data = gzip.compress(payload.encode(), mtime=0)
    digest = hashlib.sha256(data).hexdigest()
    return data, {
        "key": f"{folder}/jobs-{version}-{digest}.json.gz",
        "version": version,
        "count": len(rows),
        "bytes": len(data),
        "sha256": digest,
    }


def decode_package(data, package):
    if len(data) != package["bytes"] or hashlib.sha256(data).hexdigest() != package["sha256"]:
        raise ValueError("Public job package failed size or SHA-256 verification")
    rows = json.loads(gzip.decompress(data))
    if not isinstance(rows, list) or len(rows) != package["count"]:
        raise ValueError("Public job package record count mismatch")
    return rows
