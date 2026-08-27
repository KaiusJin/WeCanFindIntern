"""Pydantic models for the Application Tracker module."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class ApplicationStage(StrEnum):
    INTERESTED = "interested"
    APPLIED = "applied"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"


class ApplicationPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


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
    NOTE = "note"
    STAGE_CHANGE = "stage_change"
    FOLLOW_UP = "follow_up"
    INTERVIEW = "interview"
    DEADLINE = "deadline"
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
    notes: str | None = None
    applied_at: datetime | None = None
    interview_at: datetime | None = None
    offer_at: datetime | None = None
    rejected_at: datetime | None = None
    application_deadline: date | None = None
    follow_up_at: datetime | None = None
    priority: ApplicationPriority = ApplicationPriority.NORMAL
    next_step: str | None = None
    archived_at: datetime | None = None
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
    notes: str | None = None
    applied_at: datetime | None = None
    interview_at: datetime | None = None
    application_deadline: date | None = None
    follow_up_at: datetime | None = None
    source: TrackerSource = TrackerSource.OTHER
    priority: ApplicationPriority = ApplicationPriority.NORMAL
    next_step: str | None = None


class TrackerUpdateRequest(BaseModel):
    company_name: str | None = None
    title: str | None = None
    location_text: str | None = None
    work_mode: str | None = None
    job_url: str | None = None
    job_description: str | None = None
    salary_text: str | None = None
    stage: ApplicationStage | None = None
    notes: str | None = None
    applied_at: datetime | None = None
    interview_at: datetime | None = None
    offer_at: datetime | None = None
    rejected_at: datetime | None = None
    application_deadline: date | None = None
    follow_up_at: datetime | None = None
    source: TrackerSource | None = None
    priority: ApplicationPriority | None = None
    next_step: str | None = None
    archived_at: datetime | None = None


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
    archived_count: int = 0
    due_soon_count: int = 0
    stale_count: int = 0
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
    priority: ApplicationPriority | None = None
    archive: bool | None = None


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


class TrackerEventCreateRequest(BaseModel):
    event_type: TrackerEventType = TrackerEventType.NOTE
    title: str = Field(..., min_length=1, max_length=255)
    details: str | None = None
    occurred_at: datetime | None = None


class AttentionItem(BaseModel):
    application: TrackedApplication
    reason: str
    reason_type: str
    due_at: datetime | date | None = None


class TrackerNeedsAttentionResponse(BaseModel):
    items: list[AttentionItem]
    total: int = 0


class TrackerStageMetric(BaseModel):
    stage: str
    count: int
    percent: float


class TrackerWeeklyMetric(BaseModel):
    week_start: date
    count: int


class TrackerGroupMetric(BaseModel):
    label: str
    count: int


class TrackerAnalyticsResponse(BaseModel):
    stages: list[TrackerStageMetric]
    weekly_applications: list[TrackerWeeklyMetric]
    top_companies: list[TrackerGroupMetric]
    top_locations: list[TrackerGroupMetric]
    top_sources: list[TrackerGroupMetric]
    top_categories: list[TrackerGroupMetric]
    average_days_to_response: float | None = None
    application_to_interview_percent: float = 0.0
    interview_to_offer_percent: float = 0.0
