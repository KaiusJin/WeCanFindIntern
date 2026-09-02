"""Typed read contracts for the local WaterlooWorks library API."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class WaterlooWorksSalary(BaseModel):
    interval: str | None = None
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    currency: str | None = None
    source: Literal["waterlooworks"] = "waterlooworks"
    annualized_minimum: Decimal | None = None
    annualized_maximum: Decimal | None = None


class WaterlooWorksJob(BaseModel):
    """Stable public fields; extra stored classification fields remain available."""

    model_config = ConfigDict(extra="allow")

    source_job_id: str
    title: str
    organization: str | None = None
    division: str | None = None
    location_text: str | None = None
    work_mode: str = "unknown"
    application_deadline: str | None = Field(
        default=None,
        description="Toronto-local source text displayed by WaterlooWorks.",
    )
    application_url: str | None = None
    source_url: str
    description: str | None = None
    boards: list[str] = Field(default_factory=list)
    board_labels: dict[str, str] = Field(default_factory=dict)
    salary: WaterlooWorksSalary | None = None
    application_status: str | None = None
    application_submitted_at: str | None = None
    application_term: str | None = None
    application_job_status: str | None = None
    application_openings: str | None = None
    application_submitted_by: str | None = None
    submitted_application_deadline: str | None = None


class WaterlooWorksJobPage(BaseModel):
    schema_version: Literal["waterlooworks-job-page.v1"]
    items: list[WaterlooWorksJob]
    total_count: int
    last_updated_at: str | None = None
    next_cursor: str | None = None
    has_more: bool


WaterlooWorksPayload = dict[str, Any]
