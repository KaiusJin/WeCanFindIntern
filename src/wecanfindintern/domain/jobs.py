"""Stable job contracts independent from JobSpy and PostgreSQL."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from wecanfindintern.domain.classification import (
    JobCategory,
    OpportunityType,
    ScheduleType,
    classify_job,
)
from wecanfindintern.domain.location import (
    Location,
    parse_location,
)
from wecanfindintern.domain.normalization import (
    annualize_salary,
    default_salary_currency,
    ensure_utc,
    hash_text,
    normalize_company,
    normalize_employment_types,
    normalize_job_description,
    normalize_text,
    normalize_title,
    to_decimal,
)
from wecanfindintern.domain.salary import extract_salary_from_description
from wecanfindintern.domain.salary_llm import extract_salary_hybrid
from wecanfindintern.ingestion.jobspy_adapter import NormalizedJob, canonicalize_url


class WorkMode(StrEnum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class JobStatus(StrEnum):
    ACTIVE = "active"
    POSSIBLY_CLOSED = "possibly_closed"
    CLOSED = "closed"
    EXPIRED = "expired"


class SalaryRange(BaseModel):
    interval: str | None = None
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    currency: str | None = None
    source: str | None = None
    annualized_minimum: Decimal | None = None
    annualized_maximum: Decimal | None = None


class CompanyProfile(BaseModel):
    name: str | None = None
    normalized_name: str = ""
    industry: str | None = None
    website_url: str | None = None
    source_url: str | None = None
    logo_url: str | None = None
    addresses: str | None = None
    employee_count_label: str | None = None
    revenue_label: str | None = None
    description: str | None = None


class IngestionSource(BaseModel):
    source: str
    source_job_id: str | None = None
    source_url: str
    canonical_source_url: str
    direct_url: str | None = None
    canonical_direct_url: str | None = None
    source_fingerprint: str
    payload: dict[str, Any]


class DedupeKeys(BaseModel):
    company_key: str
    title_key: str
    location_key: str
    block_key: str
    description_hash: str | None = None
    direct_url_hash: str | None = None


class CanonicalJobInput(BaseModel):
    title: str
    title_normalized: str
    company: CompanyProfile
    location: Location
    work_mode: WorkMode
    employment_types: list[str] = Field(default_factory=list)
    primary_employment_type: str | None = None
    opportunity_type: OpportunityType
    schedule_types: list[ScheduleType] = Field(default_factory=list)
    primary_schedule_type: ScheduleType
    job_category: JobCategory
    job_subcategories: list[str] = Field(default_factory=list)
    skill_tags: list[str] = Field(default_factory=list)
    requirement_tags: list[str] = Field(default_factory=list)
    display_tags: list[str] = Field(default_factory=list)
    classification_version: int
    date_posted: date | None = None
    published_sort_at: datetime
    job_function: str | None = None
    description: str | None = None
    salary: SalaryRange | None = None
    source_skills: list[str] = Field(default_factory=list)
    contact_emails: list[str] = Field(default_factory=list)
    vacancy_count: int | None = None
    dedupe: DedupeKeys
    source: IngestionSource
    first_seen_at: datetime


def canonical_job_from_jobspy(
    job: NormalizedJob,
    *,
    scraped_at: datetime | None = None,
    cached_salary: SalaryRange | None = None,
    allow_salary_extraction: bool = True,
) -> CanonicalJobInput:
    """Convert the JobSpy boundary model into the long-lived business contract."""

    seen_at = ensure_utc(scraped_at or datetime.now(UTC))
    title_key = normalize_title(job.title)
    company_key = normalize_company(job.company_name)
    location = parse_location(job.location_text)
    work_mode = derive_work_mode(job)
    employment_types = normalize_employment_types(job.employment_types)
    description_hash = hash_text(job.description) if job.description else None
    canonical_direct_url = canonicalize_url(job.direct_url) if job.direct_url else None
    direct_url_hash = hash_text(canonical_direct_url) if canonical_direct_url else None
    date_sort = (
        datetime.combine(job.date_posted, time.min, tzinfo=UTC) if job.date_posted else seen_at
    )
    location_key = location.normalized or work_mode.value
    block_company = company_key or f"unknown:{title_key}"
    block_key = hash_text(f"{block_company}|{location_key}")

    salary = None
    has_structured_salary = bool(
        job.salary
        and job.salary.interval
        and (job.salary.minimum is not None or job.salary.maximum is not None)
    )
    if not allow_salary_extraction:
        pass
    elif has_structured_salary and job.salary:
        minimum = to_decimal(job.salary.minimum)
        maximum = to_decimal(job.salary.maximum)
        salary = SalaryRange(
            interval=job.salary.interval,
            minimum=minimum,
            maximum=maximum,
            currency=(
                job.salary.currency.upper()
                if job.salary.currency
                else default_salary_currency(location.country_code)
            ),
            source=job.salary.source or "provider",
            annualized_minimum=annualize_salary(minimum, job.salary.interval),
            annualized_maximum=annualize_salary(maximum, job.salary.interval),
        )
    elif cached_salary is not None:
        salary = cached_salary.model_copy(deep=True)
    else:
        regex_salary = extract_salary_from_description(
            job.description,
            country_code=location.country_code,
        )
        extracted = regex_salary
        if extracted is None:
            extracted = extract_salary_hybrid(
                job.description,
                country_code=location.country_code,
                title=job.title,
                regex_result=regex_salary,
            )
        if extracted:
            salary = SalaryRange(
                interval=extracted.interval,
                minimum=extracted.minimum,
                maximum=extracted.maximum,
                currency=extracted.currency,
                source=extracted.source,
                annualized_minimum=annualize_salary(extracted.minimum, extracted.interval),
                annualized_maximum=annualize_salary(extracted.maximum, extracted.interval),
            )

    classification = classify_job(
        title=job.title,
        description=job.description,
        employment_types=employment_types,
        source_skills=job.source_skills,
        work_mode=work_mode.value,
    )

    return CanonicalJobInput(
        title=job.title,
        title_normalized=title_key,
        company=CompanyProfile(
            name=job.company_name,
            normalized_name=company_key,
            industry=job.company.industry,
            website_url=job.company.direct_url,
            source_url=job.company.url,
            logo_url=job.company.logo_url,
            addresses=job.company.addresses,
            employee_count_label=job.company.employee_count_label,
            revenue_label=job.company.revenue_label,
            description=job.company.description,
        ),
        location=location,
        work_mode=work_mode,
        employment_types=employment_types,
        primary_employment_type=employment_types[0] if employment_types else None,
        opportunity_type=classification.opportunity_type,
        schedule_types=classification.schedule_types,
        primary_schedule_type=classification.primary_schedule_type,
        job_category=classification.job_category,
        job_subcategories=classification.job_subcategories,
        skill_tags=classification.skill_tags,
        requirement_tags=classification.requirement_tags,
        display_tags=classification.display_tags,
        classification_version=classification.classification_version,
        date_posted=job.date_posted,
        published_sort_at=date_sort,
        job_function=job.job_function,
        description=job.description,
        salary=salary,
        source_skills=job.source_skills,
        contact_emails=job.contact_emails,
        vacancy_count=job.vacancy_count,
        dedupe=DedupeKeys(
            company_key=company_key,
            title_key=title_key,
            location_key=location_key,
            block_key=block_key,
            description_hash=description_hash,
            direct_url_hash=direct_url_hash,
        ),
        source=IngestionSource(
            source=job.source,
            source_job_id=job.source_job_id,
            source_url=job.source_url,
            canonical_source_url=canonicalize_url(job.source_url),
            direct_url=job.direct_url,
            canonical_direct_url=canonical_direct_url,
            source_fingerprint=job.source_fingerprint,
            payload=job.raw,
        ),
        first_seen_at=seen_at,
    )


def normalize_canonical_job_description(job: CanonicalJobInput) -> CanonicalJobInput:
    """Normalize the winning canonical JD after deduplication has completed."""

    description = normalize_job_description(job.description)
    description_hash = hash_text(description) if description else None
    if description == job.description and description_hash == job.dedupe.description_hash:
        return job
    return job.model_copy(
        update={
            "description": description,
            "dedupe": job.dedupe.model_copy(
                update={"description_hash": description_hash}
            ),
        }
    )


def derive_work_mode(job: NormalizedJob) -> WorkMode:
    source_value = normalize_text(job.work_from_home_type)
    if "hybrid" in source_value:
        return WorkMode.HYBRID
    if "remote" in source_value or job.is_remote is True:
        return WorkMode.REMOTE
    inferred = infer_work_mode_from_text(job.description)
    if inferred:
        return inferred
    # False only means the source did not classify it as remote; it does not prove onsite.
    return WorkMode.UNKNOWN


def infer_work_mode_from_text(description: str | None) -> WorkMode | None:
    """Infer only explicit work-mode wording from a job description."""

    text = normalize_text(description)
    if not text:
        return None
    if "hybrid" in text:
        return WorkMode.HYBRID
    if any(
        token in text
        for token in ("fully remote", "100 remote", "work remotely", "remote position")
    ):
        return WorkMode.REMOTE
    if any(token in text for token in ("on site", "onsite", "in person", "work at our")):
        return WorkMode.ONSITE
    return None
