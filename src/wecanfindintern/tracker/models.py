"""Pydantic models for the Application Tracker module."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class ApplicationStage(StrEnum):
    INTERESTED = "interested"
    APPLIED = "applied"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"


class TrackerOrigin(StrEnum):
    PLATFORM_BOOKMARK = "platform_bookmark"
    CUSTOM = "custom"


class TrackerSource(StrEnum):
    WECAN_FIND_INTERN = "wecanfindintern"
    LINKEDIN = "linkedin"
    INDEED = "indeed"
    WATERLOO_WORK = "waterloo_work"
    OTHER = "other"


class TrackerEventType(StrEnum):
    STAGE_CHANGE = "stage_change"
    CREATED = "created"


class TrackedApplication(BaseModel):
    id: UUID
    job_id: UUID | None = None
    company_name: str
    title: str
    location_text: str | None = None
    work_mode: str | None = None
    job_url: str | None = None
    job_description: str | None = None
    salary_text: str | None = None
    origin_type: TrackerOrigin = TrackerOrigin.CUSTOM
    source: TrackerSource = TrackerSource.OTHER
    stage: ApplicationStage = ApplicationStage.INTERESTED
    applied_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TrackerCreateRequest(BaseModel):
    company_name: str = Field(..., min_length=1, max_length=255)
    title: str = Field(..., min_length=1, max_length=255)
    location_text: str | None = None
    work_mode: str | None = None
    job_url: str | None = None
    job_description: str | None = None
    salary_text: str | None = None
    stage: ApplicationStage = ApplicationStage.INTERESTED
    applied_at: datetime | None = None
    source: TrackerSource = TrackerSource.OTHER


class TrackerUpdateRequest(BaseModel):
    company_name: str | None = None
    title: str | None = None
    location_text: str | None = None
    work_mode: str | None = None
    job_url: str | None = None
    job_description: str | None = None
    salary_text: str | None = None
    stage: ApplicationStage | None = None
    applied_at: datetime | None = None
    source: TrackerSource | None = None


class TrackedJobState(BaseModel):
    job_id: UUID
    application_id: UUID
    stage: ApplicationStage


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
