"""Source-neutral posting contract shared by ingestion adapters."""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field


class Salary(BaseModel):
    interval: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    currency: str | None = None
    source: str | None = None


class CompanyDetails(BaseModel):
    industry: str | None = None
    url: str | None = None
    direct_url: str | None = None
    logo_url: str | None = None
    addresses: str | None = None
    employee_count_label: str | None = None
    revenue_label: str | None = None
    description: str | None = None
    rating: float | None = None
    reviews_count: int | None = None


class NormalizedJob(BaseModel):
    """Stable provider-independent input used by canonicalization."""

    source_fingerprint: str
    source: str
    source_job_id: str | None = None
    source_url: str
    direct_url: str | None = None
    title: str
    company_name: str | None = None
    location_text: str | None = None
    date_posted: date | None = None
    employment_types: list[str] = Field(default_factory=list)
    is_remote: bool | None = None
    job_function: str | None = None
    description: str | None = None
    contact_emails: list[str] = Field(default_factory=list)
    salary: Salary | None = None
    company: CompanyDetails = Field(default_factory=CompanyDetails)
    source_skills: list[str] = Field(default_factory=list)
    vacancy_count: int | None = None
    work_from_home_type: str | None = None
    raw: dict[str, Any]


def build_source_fingerprint(
    source: str, source_id: str | None, source_url: str
) -> str:
    identity = source_id or canonicalize_url(source_url)
    return hashlib.sha256(f"{source}:{identity}".encode()).hexdigest()


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    kept_query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in {"trk", "trackingid", "ref", "refid"}
    ]
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/"),
            urlencode(kept_query),
            "",
        )
    )
