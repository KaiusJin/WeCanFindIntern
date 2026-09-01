"""Pydantic models for the Application Tracker module."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from wecanfindintern.domain.location import clean_location_display


class ApplicationStage(StrEnum):
    INTERESTED = "interested"
    APPLIED = "applied"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"


APPLICATION_STAGE_LABELS: dict[ApplicationStage, str] = {
    ApplicationStage.INTERESTED: "Interested",
    ApplicationStage.APPLIED: "Applied",
    ApplicationStage.INTERVIEW: "Interview",
    ApplicationStage.OFFER: "Offer",
    ApplicationStage.REJECTED: "Rejected",
}


def application_stage_label(stage: ApplicationStage | str) -> str:
    """Return the one user-facing label for an application stage."""

    try:
        normalized = stage if isinstance(stage, ApplicationStage) else ApplicationStage(stage)
    except ValueError:
        return str(stage).replace("_", " ").title()
    return APPLICATION_STAGE_LABELS[normalized]


class TrackerOrigin(StrEnum):
    PLATFORM_BOOKMARK = "platform_bookmark"
    CUSTOM = "custom"


class TrackerSource(StrEnum):
    WECAN_FIND_INTERN = "wecanfindintern"
    LINKEDIN = "linkedin"
    INDEED = "indeed"
    GLASSDOOR = "glassdoor"
    ZIP_RECRUITER = "zip_recruiter"
    GOOGLE = "google"
    WATERLOO_WORK = "waterloo_work"
    OTHER = "other"


TRACKER_SOURCE_LABELS: dict[TrackerSource, str] = {
    TrackerSource.WECAN_FIND_INTERN: "WeCanFindIntern",
    TrackerSource.LINKEDIN: "LinkedIn",
    TrackerSource.INDEED: "Indeed",
    TrackerSource.GLASSDOOR: "Glassdoor",
    TrackerSource.ZIP_RECRUITER: "ZipRecruiter",
    TrackerSource.GOOGLE: "Google Jobs",
    TrackerSource.WATERLOO_WORK: "WaterlooWorks",
    TrackerSource.OTHER: "Other",
}


class TrackerContractResponse(BaseModel):
    stages: dict[str, str]
    sources: dict[str, str]


class TrackerEventType(StrEnum):
    STAGE_CHANGE = "stage_change"
    EXTERNAL_STATUS = "external_status"
    CREATED = "created"


class TrackedApplication(BaseModel):
    id: UUID
    job_id: UUID | None = None
    external_job_id: str | None = None
    company_name: str
    title: str
    location_text: str | None = None
    work_mode: str | None = None
    job_url: str | None = None
    job_description: str | None = None
    application_deadline: date | None = None
    salary_text: str | None = None
    origin_type: TrackerOrigin = TrackerOrigin.CUSTOM
    source: TrackerSource = TrackerSource.OTHER
    stage: ApplicationStage = ApplicationStage.INTERESTED
    external_stage: ApplicationStage | None = None
    external_status: str | None = None
    applied_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("location_text")
    @classmethod
    def display_clean_location(cls, value: str | None) -> str | None:
        return clean_location_display(value)


class TrackerCreateRequest(BaseModel):
    company_name: str = Field(..., min_length=1, max_length=255)
    title: str = Field(..., min_length=1, max_length=255)
    location_text: str | None = None
    work_mode: str | None = None
    job_url: str | None = None
    job_description: str | None = None
    application_deadline: date | None = None
    salary_text: str | None = None
    stage: ApplicationStage = ApplicationStage.INTERESTED
    applied_at: datetime | None = None
    source: TrackerSource = TrackerSource.OTHER


class TrackerUpdateRequest(BaseModel):
    company_name: str | None = Field(default=None, min_length=1, max_length=255)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    location_text: str | None = None
    work_mode: str | None = None
    job_url: str | None = None
    job_description: str | None = None
    application_deadline: date | None = None
    salary_text: str | None = None
    stage: ApplicationStage | None = None
    applied_at: datetime | None = None
    source: TrackerSource | None = None


class TrackedJobState(BaseModel):
    job_id: UUID
    application_id: UUID
    stage: ApplicationStage


class TrackedExternalJobState(BaseModel):
    external_job_id: str
    application_id: UUID
    stage: ApplicationStage
    source: TrackerSource = TrackerSource.WATERLOO_WORK


class TrackerStatsResponse(BaseModel):
    total: int = 0
    interested_count: int = 0
    applied_count: int = 0
    interview_count: int = 0
    offer_count: int = 0
    rejected_count: int = 0
    response_rate_percent: float = 0.0


class TrackerListResponse(BaseModel):
    items: list[TrackedApplication]
    stats: TrackerStatsResponse
    total: int = 0
    page: int = 1
    page_size: int = 50
    total_pages: int = 0


class TrackerBulkUpdateRequest(BaseModel):
    ids: list[UUID] = Field(..., min_length=1, max_length=500)
    stage: ApplicationStage | None = None


class TrackerBulkDeleteRequest(BaseModel):
    ids: list[UUID] = Field(..., min_length=1, max_length=500)


class TrackerBulkResult(BaseModel):
    updated: int = 0
    deleted: int = 0


class TrackerEvent(BaseModel):
    id: UUID
    application_id: UUID
    event_type: TrackerEventType
    title: str
    details: str | None = None
    occurred_at: datetime
    created_at: datetime
