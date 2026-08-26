"""Stable job contracts independent from JobSpy and PostgreSQL."""

from __future__ import annotations

import hashlib
import re
import unicodedata
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


class Location(BaseModel):
    raw: str | None = None
    city: str | None = None
    region_code: str | None = None
    region_name: str | None = None
    region_type: str | None = None
    country_code: str | None = None
    country_name: str | None = None
    normalized: str = ""


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


EMPLOYMENT_TYPE_MAP = {
    "fulltime": "full_time",
    "full time": "full_time",
    "parttime": "part_time",
    "part time": "part_time",
    "internship": "internship",
    "intern": "internship",
    "contract": "contract",
    "temporary": "temporary",
}

COUNTRY_ALIASES = {
    "ca": "CA",
    "canada": "CA",
    "us": "US",
    "usa": "US",
    "united states": "US",
    "uk": "GB",
    "united kingdom": "GB",
    "fr": "FR",
    "france": "FR",
}

CANADIAN_REGION_ALIASES = {
    "alberta": "AB",
    "ab": "AB",
    "british columbia": "BC",
    "bc": "BC",
    "manitoba": "MB",
    "mb": "MB",
    "new brunswick": "NB",
    "nb": "NB",
    "newfoundland and labrador": "NL",
    "newfoundland": "NL",
    "nl": "NL",
    "northwest territories": "NT",
    "nt": "NT",
    "nova scotia": "NS",
    "ns": "NS",
    "nunavut": "NU",
    "nu": "NU",
    "ontario": "ON",
    "ontraio": "ON",
    "ontairo": "ON",
    "on": "ON",
    "prince edward island": "PE",
    "pei": "PE",
    "pe": "PE",
    "quebec": "QC",
    "québec": "QC",
    "qc": "QC",
    "saskatchewan": "SK",
    "sk": "SK",
    "yukon": "YT",
    "yt": "YT",
}

US_REGION_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin",
    "WY": "Wyoming", "DC": "District of Columbia",
}

US_REGION_ALIASES = {
    alias: code
    for code, name in US_REGION_NAMES.items()
    for alias in (code.casefold(), name.casefold())
}
US_REGION_ALIASES.update({
    "washington dc": "DC",
    "washington d c": "DC",
    "district columbia": "DC",
})

CANADIAN_REGION_NAMES = {
    "AB": "Alberta",
    "BC": "British Columbia",
    "MB": "Manitoba",
    "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador",
    "NT": "Northwest Territories",
    "NS": "Nova Scotia",
    "NU": "Nunavut",
    "ON": "Ontario",
    "PE": "Prince Edward Island",
    "QC": "Quebec",
    "SK": "Saskatchewan",
    "YT": "Yukon",
}

COUNTRY_NAMES = {
    "CA": "Canada",
    "US": "United States",
    "GB": "United Kingdom",
    "FR": "France",
}

CANADIAN_CITY_NAMES = {
    ("QC", "montreal"): "Montréal",
    ("QC", "quebec"): "Québec",
}

COMPANY_SUFFIXES = re.compile(
    r"\b(?:incorporated|inc|limited|ltd|corporation|corp|company|co|llc|plc)\.?$"
)


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


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def normalize_company(value: str | None) -> str:
    normalized = normalize_text(value)
    previous = None
    while normalized and normalized != previous:
        previous = normalized
        normalized = COMPANY_SUFFIXES.sub("", normalized).strip()
    return normalized


def normalize_title(value: str) -> str:
    normalized = normalize_text(value)
    tokens = normalized.split()
    expansions = {
        "swe": ["software", "engineer"],
        "sr": ["senior"],
        "jr": ["junior"],
        "dev": ["developer"],
    }
    expanded: list[str] = []
    for token in tokens:
        expanded.extend(expansions.get(token, [token]))
    return " ".join(expanded)


def normalize_employment_types(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        normalized_value = normalize_text(value)
        mapped = EMPLOYMENT_TYPE_MAP.get(
            normalized_value,
            normalized_value.replace(" ", "_"),
        )
        if mapped and mapped not in normalized:
            normalized.append(mapped)
    return normalized


def parse_location(value: str | None) -> Location:
    if not value:
        return Location()
    raw = value.strip()
    if normalize_text(raw) in {"remote", "worldwide"}:
        return Location(raw=raw, normalized="remote")
    country_only = COUNTRY_ALIASES.get(normalize_text(raw))
    if country_only:
        return Location(
            raw=raw,
            country_code=country_only,
            country_name=COUNTRY_NAMES.get(country_only),
            normalized=normalize_text(country_only),
        )

    parts = [part.strip() for part in raw.split(",") if part.strip()]
    city_raw = parts[0] if parts else None
    region_raw = parts[-2] if len(parts) >= 2 else None
    country_token = normalize_text(parts[-1]) if len(parts) >= 2 else ""
    country = COUNTRY_ALIASES.get(country_token)

    if country is None and len(parts) == 2:
        # JobSpy sometimes returns "City, Region" without a country.
        region_raw = parts[-1]
        normalized_region = normalize_text(region_raw)
        if normalized_region in CANADIAN_REGION_ALIASES:
            country = "CA"
        elif normalized_region in US_REGION_ALIASES:
            country = "US"
    elif country is None and len(parts) >= 3 and len(parts[-1]) == 2:
        country = parts[-1].upper()

    region = normalize_region_code(region_raw, country)
    region_name = normalize_region_name(region_raw, region, country)
    region_type = derive_region_type(region, country)
    country_name = COUNTRY_NAMES.get(country) if country else None
    city = normalize_city_name(city_raw, region, country)

    normalized_parts = [normalize_text(item) for item in (city, region, country) if item]
    return Location(
        raw=raw,
        city=city,
        region_code=region,
        region_name=region_name,
        region_type=region_type,
        country_code=country,
        country_name=country_name,
        normalized="|".join(normalized_parts),
    )


def normalize_region_code(value: str | None, country_code: str | None) -> str | None:
    if not value:
        return None
    normalized = normalize_text(value)
    if country_code == "CA" or normalized in CANADIAN_REGION_ALIASES:
        return CANADIAN_REGION_ALIASES.get(normalized, value.strip().upper())
    if country_code == "US" or normalized in US_REGION_ALIASES:
        return US_REGION_ALIASES.get(normalized, value.strip().upper())
    return value.strip().upper()


def normalize_city_name(
    raw_value: str | None,
    region_code: str | None,
    country_code: str | None,
) -> str | None:
    if not raw_value:
        return None
    value = raw_value.strip()
    if country_code == "CA" and region_code:
        ascii_key = "".join(
            character
            for character in unicodedata.normalize("NFKD", value).casefold()
            if not unicodedata.combining(character)
        )
        return CANADIAN_CITY_NAMES.get((region_code, ascii_key), value)
    return value


def normalize_region_name(
    raw_value: str | None,
    region_code: str | None,
    country_code: str | None,
) -> str | None:
    if country_code == "CA" and region_code:
        return CANADIAN_REGION_NAMES.get(region_code, raw_value)
    if country_code == "US" and region_code:
        return US_REGION_NAMES.get(region_code, raw_value)
    return raw_value.strip() if raw_value else None


def derive_region_type(region_code: str | None, country_code: str | None) -> str | None:
    if not region_code:
        return None
    if country_code == "CA":
        return "territory" if region_code in {"NT", "NU", "YT"} else "province"
    if country_code == "US":
        return "state"
    return "region"


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
    if any(token in text for token in ("fully remote", "100 remote", "work remotely", "remote position")):
        return WorkMode.REMOTE
    if any(token in text for token in ("on site", "onsite", "in person", "work at our")):
        return WorkMode.ONSITE
    return None


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def to_decimal(value: float | None) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


SALARY_ANNUALIZATION_FACTORS = {
    "hourly": Decimal("2080"),
    "daily": Decimal("260"),
    "weekly": Decimal("52"),
    "monthly": Decimal("12"),
    "yearly": Decimal("1"),
    "annual": Decimal("1"),
}


def annualize_salary(value: Decimal | None, interval: str | None) -> Decimal | None:
    if value is None or not interval:
        return None
    factor = SALARY_ANNUALIZATION_FACTORS.get(normalize_text(interval))
    return (value * factor).quantize(Decimal("0.01")) if factor else None


def default_salary_currency(country_code: str | None) -> str:
    return {
        "CA": "CAD",
        "US": "USD",
        "GB": "GBP",
    }.get(country_code or "", "USD")


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
