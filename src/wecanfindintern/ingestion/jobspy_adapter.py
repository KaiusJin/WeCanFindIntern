"""Boundary between JobSpy's DataFrame output and our stable job contract."""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping
from contextlib import suppress
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any, Literal

import pandas as pd
from jobspy import scrape_jobs
from jobspy.linkedin import LinkedIn
from jobspy.model import DescriptionFormat
from jobspy.util import extract_emails_from_text
from pydantic import BaseModel, Field, field_validator, model_validator

from wecanfindintern.domain.normalized_job import (
    CompanyDetails,
    NormalizedJob,
    Salary,
    build_source_fingerprint,
    canonicalize_url,
)

__all__ = [
    "CompanyDetails",
    "NormalizedJob",
    "Salary",
    "build_source_fingerprint",
    "canonicalize_url",
    "fetch_linkedin_details",
    "merge_linkedin_details",
]

SUPPORTED_SITES = {
    "linkedin",
    "indeed",
    "zip_recruiter",
    "glassdoor",
    "google",
    "bayt",
    "naukri",
    "bdjobs",
}

# Mirrors jobspy.util.desired_order in JobSpy 1.1.82.
JOBSPY_COLUMNS: tuple[str, ...] = (
    "id",
    "site",
    "job_url",
    "job_url_direct",
    "title",
    "company",
    "location",
    "date_posted",
    "job_type",
    "salary_source",
    "interval",
    "min_amount",
    "max_amount",
    "currency",
    "is_remote",
    "job_level",
    "job_function",
    "listing_type",
    "emails",
    "description",
    "company_industry",
    "company_url",
    "company_logo",
    "company_url_direct",
    "company_addresses",
    "company_num_employees",
    "company_revenue",
    "company_description",
    "skills",
    "experience_range",
    "company_rating",
    "company_reviews_count",
    "vacancy_count",
    "work_from_home_type",
)


class JobSpyQuery(BaseModel):
    """Validated subset of JobSpy arguments used by this project."""

    sites: list[str] = Field(default_factory=lambda: ["indeed"])
    search_term: str
    google_search_term: str | None = None
    location: str | None = None
    distance: int = Field(default=50, ge=0)
    is_remote: bool = False
    job_type: Literal["fulltime", "parttime", "internship", "contract"] | None = None
    easy_apply: bool | None = None
    results_wanted: int = Field(default=20, ge=1, le=1000)
    country_indeed: str = "Canada"
    description_format: Literal["markdown", "html", "plain"] = "markdown"
    linkedin_fetch_description: bool = False
    offset: int = Field(default=0, ge=0)
    hours_old: int | None = Field(default=None, ge=1)
    enforce_annual_salary: bool = False
    proxies: list[str] | None = None
    ca_cert: str | None = None
    user_agent: str | None = None
    verbose: int = Field(default=1, ge=0, le=2)

    @field_validator("sites")
    @classmethod
    def validate_sites(cls, sites: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(site.strip().lower() for site in sites))
        if not normalized:
            raise ValueError("At least one job source is required")
        unknown = sorted(set(normalized) - SUPPORTED_SITES)
        if unknown:
            raise ValueError(f"Unsupported JobSpy sources: {', '.join(unknown)}")
        return normalized

    @model_validator(mode="after")
    def validate_upstream_filter_combinations(self) -> JobSpyQuery:
        if "google" in self.sites and not self.google_search_term:
            raise ValueError("google_search_term is required when using Google Jobs")

        if "indeed" in self.sites:
            indeed_filter_groups = sum(
                (
                    self.hours_old is not None,
                    self.job_type is not None or self.is_remote,
                    self.easy_apply is not None,
                )
            )
            if indeed_filter_groups > 1:
                raise ValueError(
                    "Indeed accepts only one of: hours_old, job_type/is_remote, easy_apply"
                )

        if "linkedin" in self.sites and self.hours_old is not None and self.easy_apply is not None:
            raise ValueError("LinkedIn cannot combine hours_old with easy_apply")
        return self


class ScrapeResult(BaseModel):
    query: JobSpyQuery
    raw_columns: list[str]
    raw_row_count: int
    jobs: list[NormalizedJob]


def scrape_and_normalize(query: JobSpyQuery) -> tuple[pd.DataFrame, ScrapeResult]:
    """Run JobSpy once and return both the source DataFrame and stable records."""

    frame = scrape_jobs(
        site_name=query.sites,
        search_term=query.search_term,
        google_search_term=query.google_search_term,
        location=query.location,
        distance=query.distance,
        is_remote=query.is_remote,
        job_type=query.job_type,
        easy_apply=query.easy_apply,
        results_wanted=query.results_wanted,
        country_indeed=query.country_indeed,
        proxies=query.proxies,
        ca_cert=query.ca_cert,
        description_format=query.description_format,
        linkedin_fetch_description=query.linkedin_fetch_description,
        offset=query.offset,
        hours_old=query.hours_old,
        enforce_annual_salary=query.enforce_annual_salary,
        verbose=query.verbose,
        user_agent=query.user_agent,
    )
    stable_frame = stabilize_jobspy_frame(frame)
    records = dataframe_to_records(stable_frame)
    jobs = [normalize_record(record) for record in records]
    return stable_frame, ScrapeResult(
        query=query,
        raw_columns=list(stable_frame.columns),
        raw_row_count=len(stable_frame),
        jobs=jobs,
    )


def scrape_checked(query: JobSpyQuery):
    """Convert JobSpy's logged source failure into a retryable exception.

    Some JobSpy scrapers log an HTTP/parsing error and return an empty DataFrame
    instead of raising. A genuinely empty search has no error log and remains a
    successful terminal page.
    """

    errors: list[str] = []

    class JobSpyErrorCapture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.levelno >= logging.ERROR and record.name.startswith("JobSpy"):
                errors.append(record.getMessage())

    handler = JobSpyErrorCapture()
    root_logger = logging.getLogger()
    jobspy_loggers = [
        logging.getLogger(name)
        for name in logging.root.manager.loggerDict
        if name.startswith("JobSpy:")
    ]
    root_logger.addHandler(handler)
    for logger in jobspy_loggers:
        logger.addHandler(handler)
    try:
        frame, result = scrape_and_normalize(query)
    finally:
        root_logger.removeHandler(handler)
        for logger in jobspy_loggers:
            logger.removeHandler(handler)
    if not result.jobs and errors:
        raise RuntimeError("; ".join(errors[-3:]))
    return frame, result


def stabilize_jobspy_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Guarantee the documented column contract, including zero-result runs."""

    stable = frame.copy()
    for column in JOBSPY_COLUMNS:
        if column not in stable.columns:
            stable[column] = None
    return stable.loc[:, JOBSPY_COLUMNS]


def dataframe_to_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {key: clean_scalar(value) for key, value in row.items()}
        for row in frame.to_dict(orient="records")
    ]


def clean_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Enum):
        enum_value = value.value
        # JobSpy's JobType values are tuples of localized aliases. The first
        # entry is its canonical wire value (for example, "fulltime").
        if isinstance(enum_value, tuple) and enum_value:
            enum_value = enum_value[0]
        return clean_scalar(enum_value)
    if isinstance(value, Mapping):
        return {str(key): clean_scalar(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [clean_scalar(item) for item in value]
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        missing = pd.isna(value)
        if missing is pd.NA:
            return None
        try:
            if bool(missing):
                return None
        except (TypeError, ValueError):
            # Array-like values are not expected in the flattened DataFrame.
            pass
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        with suppress(ValueError, AttributeError):
            value = value.item()
            return clean_scalar(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def normalize_record(record: dict[str, Any]) -> NormalizedJob:
    record = {key: clean_scalar(value) for key, value in record.items()}
    source = _required_text(record.get("site"), "site")
    source_url = _required_text(record.get("job_url"), "job_url")
    title = _required_text(record.get("title"), "title")
    source_id = optional_text(record.get("id"))

    salary = Salary(
        interval=optional_text(record.get("interval")),
        minimum=optional_float(record.get("min_amount")),
        maximum=optional_float(record.get("max_amount")),
        currency=optional_text(record.get("currency")),
        source=optional_text(record.get("salary_source")),
    )
    if not any(value is not None for value in salary.model_dump().values()):
        salary = None

    return NormalizedJob(
        source_fingerprint=build_source_fingerprint(source, source_id, source_url),
        source=source,
        source_job_id=source_id,
        source_url=source_url,
        direct_url=optional_text(record.get("job_url_direct")),
        title=title,
        company_name=optional_text(record.get("company")),
        location_text=optional_text(record.get("location")),
        date_posted=optional_date(record.get("date_posted")),
        employment_types=split_multi_value(record.get("job_type")),
        is_remote=optional_bool(record.get("is_remote")),
        job_function=optional_text(record.get("job_function")),
        description=optional_text(record.get("description")),
        contact_emails=split_multi_value(record.get("emails")),
        salary=salary,
        company=CompanyDetails(
            industry=optional_text(record.get("company_industry")),
            url=optional_text(record.get("company_url")),
            direct_url=optional_text(record.get("company_url_direct")),
            logo_url=optional_text(record.get("company_logo")),
            addresses=optional_text(record.get("company_addresses")),
            employee_count_label=optional_text(record.get("company_num_employees")),
            revenue_label=optional_text(record.get("company_revenue")),
            description=optional_text(record.get("company_description")),
            rating=optional_float(record.get("company_rating")),
            reviews_count=optional_int(record.get("company_reviews_count")),
        ),
        source_skills=split_multi_value(record.get("skills")),
        vacancy_count=optional_int(record.get("vacancy_count")),
        work_from_home_type=optional_text(record.get("work_from_home_type")),
        raw=record,
    )


LINKEDIN_DETAIL_FIELDS: tuple[str, ...] = (
    "description",
    "job_level",
    "company_industry",
    "job_type",
    "job_url_direct",
    "company_logo",
    "job_function",
    "emails",
)


def fetch_linkedin_details(
    source_job_id: str,
    *,
    description_format: Literal["markdown", "html", "plain"] = "markdown",
    proxies: list[str] | str | None = None,
    ca_cert: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Fetch one LinkedIn detail page after campaign-level ID deduplication."""

    job_id = source_job_id.removeprefix("li-")
    scraper = LinkedIn(proxies=proxies, ca_cert=ca_cert, user_agent=user_agent)
    return scraper.get_job_details(job_id, DescriptionFormat(description_format))


def merge_linkedin_details(
    job: NormalizedJob,
    details: dict[str, Any],
    *,
    fetched_at: datetime | None = None,
) -> NormalizedJob:
    """Overlay cached/fresh detail fields while retaining current search-card metadata."""

    raw = dict(job.raw)
    for field in LINKEDIN_DETAIL_FIELDS:
        value = details.get(field)
        if value is not None and value != "":
            raw[field] = clean_scalar(value)
    if raw.get("description") and not raw.get("emails"):
        raw["emails"] = extract_emails_from_text(raw["description"])
    merged = normalize_record(raw)
    return merged.model_copy(update={"details_fetched_at": fetched_at or datetime.now(UTC)})


def split_multi_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, Iterable) and not isinstance(value, (bytes, dict)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def optional_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()


def _required_text(value: Any, field_name: str) -> str:
    text = optional_text(value)
    if text is None:
        raise ValueError(f"JobSpy record is missing required field: {field_name}")
    return text


def optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def optional_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return bool(value)
