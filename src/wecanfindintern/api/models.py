"""Public API models. Raw provider payloads are never exposed here."""

from __future__ import annotations

import base64
import binascii
import json
import math
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class JobListFilters(BaseModel):
    query: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    countries: list[str] = Field(default_factory=list)
    region: str | None = Field(default=None, max_length=32)
    regions: list[str] = Field(default_factory=list)
    city: str | None = Field(default=None, max_length=120)
    cities: list[str] = Field(default_factory=list)
    company: str | None = Field(default=None, max_length=160)
    work_mode: Literal["onsite", "hybrid", "remote", "unknown"] | None = None
    work_modes: list[Literal["onsite", "hybrid", "remote", "unknown"]] = Field(
        default_factory=list
    )
    employment_type: str | None = Field(default=None, max_length=40)
    opportunity_type: str | None = Field(default=None, max_length=40)
    opportunity_types: list[str] = Field(default_factory=list)
    schedule_type: str | None = Field(default=None, max_length=40)
    schedule_types: list[str] = Field(default_factory=list)
    category: str | None = Field(default=None, max_length=60)
    categories: list[str] = Field(default_factory=list)
    subcategory: str | None = Field(default=None, max_length=60)
    skill: str | None = Field(default=None, max_length=80)
    skills: list[str] = Field(default_factory=list)
    season: Literal["winter", "spring", "summer", "fall"] | None = None
    recruiting_year: int | None = Field(default=None, ge=2020, le=2099)
    recruiting_term: str | None = Field(default=None, max_length=40)
    recruiting_terms: list[str] = Field(default_factory=list)
    has_recruiting_term: bool | None = None
    source: str | None = Field(default=None, max_length=40)
    posted_after: date | None = None
    salary_min: Decimal | None = Field(default=None, ge=0)
    annual_salary_min: Decimal | None = Field(default=None, ge=0)
    annual_salary_max: Decimal | None = Field(default=None, ge=0)
    hourly_salary_min: Decimal | None = Field(default=None, ge=0)
    hourly_salary_max: Decimal | None = Field(default=None, ge=0)
    has_salary: bool | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    sort_by_relevance: bool = False
    cursor: str | None = Field(default=None, max_length=256)
    limit: int = Field(default=30, ge=1, le=100)

    @field_validator("country", "region", "currency")
    @classmethod
    def uppercase_codes(cls, value: str | None) -> str | None:
        return value.upper() if value else None

    @field_validator("countries", "regions")
    @classmethod
    def uppercase_code_lists(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip().upper() for value in values if value.strip()))

    @field_validator(
        "cities",
        "opportunity_types",
        "schedule_types",
        "categories",
        "skills",
        "recruiting_terms",
    )
    @classmethod
    def clean_filter_lists(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @field_validator("season", mode="before")
    @classmethod
    def normalize_season(cls, value: str | None) -> str | None:
        if not value:
            return None
        v = value.strip().lower()
        aliases = {
            "winter": "winter",
            "spring": "spring",
            "summer": "summer",
            "fall": "fall",
            "autumn": "fall",
        }
        return aliases.get(v, v)

    @model_validator(mode="after")
    def populate_from_recruiting_term(self) -> JobListFilters:
        if self.recruiting_term and (self.season is None or self.recruiting_year is None):
            parts = self.recruiting_term.replace("_", " ").split()
            if len(parts) == 2:
                s_cand, y_cand = parts[0].lower(), parts[1]
                if s_cand in ("winter", "spring", "summer", "fall", "autumn"):
                    season_val = "fall" if s_cand == "autumn" else s_cand
                    try:
                        year_val = int(y_cand)
                        if 2020 <= year_val <= 2099:
                            if self.season is None:
                                self.season = season_val  # type: ignore
                            if self.recruiting_year is None:
                                self.recruiting_year = year_val
                    except ValueError:
                        pass
        return self


class SalaryResponse(BaseModel):
    interval: str | None
    minimum: Decimal | None
    maximum: Decimal | None
    currency: str | None
    source: str | None
    annualized_minimum: Decimal | None
    annualized_maximum: Decimal | None


class RecruitingTermResponse(BaseModel):
    season: Literal["winter", "spring", "summer", "fall"]
    year: int
    display_name: str


class LocationResponse(BaseModel):
    text: str | None
    display_name: str | None
    city: str | None
    region: str | None
    region_code: str | None
    region_name: str | None
    region_type: str | None
    country: str | None
    country_code: str | None
    country_name: str | None


class JobListItem(BaseModel):
    schema_version: Literal["job.v3"] = "job.v3"
    id: UUID
    title: str
    company_name: str | None
    location: LocationResponse
    work_mode: str
    employment_types: list[str]
    opportunity_type: str
    schedule_types: list[str]
    primary_schedule_type: str
    job_category: str
    job_subcategories: list[str]
    date_posted: date | None
    published_at: datetime
    salary: SalaryResponse | None
    recruiting_term: RecruitingTermResponse | None
    skill_tags: list[str]
    display_tags: list[str]
    source_count: int
    first_seen_at: datetime
    last_seen_at: datetime


class JobSourceResponse(BaseModel):
    source: str
    source_job_id: str | None
    url: str
    direct_url: str | None
    first_seen_at: datetime
    last_seen_at: datetime


class JobDetail(JobListItem):
    description: str | None
    job_function: str | None
    company_industry: str | None
    company_website_url: str | None
    company_logo_url: str | None
    skills: list[str]
    requirement_tags: list[str]
    classification_version: int
    contact_emails: list[str]
    vacancy_count: int | None
    sources: list[JobSourceResponse]


class JobPage(BaseModel):
    schema_version: Literal["job-page.v3"] = "job-page.v3"
    items: list[JobListItem]
    total_count: int | None = None
    last_updated_at: datetime | None = None
    next_cursor: str | None
    has_more: bool


class FacetCount(BaseModel):
    value: str
    count: int


class JobFacetsResponse(BaseModel):
    schema_version: Literal["job-facets.v2"] = "job-facets.v2"
    opportunity_types: list[FacetCount]
    schedule_types: list[FacetCount]
    job_categories: list[FacetCount]
    work_modes: list[FacetCount]
    skills: list[FacetCount]
    countries: list[FacetCount]
    regions: list[FacetCount]
    cities: list[FacetCount]
    companies: list[FacetCount]
    recruiting_terms: list[FacetCount] = Field(default_factory=list)
    recruiting_seasons: list[FacetCount] = Field(default_factory=list)
    last_updated_at: datetime | None = None


def encode_cursor(
    published_at: datetime, row_id: int, *, relevance: float | None = None
) -> str:
    cursor_payload: dict[str, str | int | float] = {
        "published_at": published_at.isoformat(),
        "id": row_id,
    }
    if relevance is not None:
        cursor_payload["relevance"] = relevance
    payload = json.dumps(cursor_payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(value: str) -> tuple[datetime, int, float | None]:
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(value + padding))
        published_at = datetime.fromisoformat(payload["published_at"])
        row_id = int(payload["id"])
        relevance = (
            float(payload["relevance"]) if payload.get("relevance") is not None else None
        )
        if published_at.tzinfo is None or row_id < 1 or (
            relevance is not None and not math.isfinite(relevance)
        ):
            raise ValueError
        return published_at, row_id, relevance
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, binascii.Error) as error:
        raise ValueError("Invalid pagination cursor") from error
