"""Pydantic models for the Application Tracker module."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ApplicationStage(StrEnum):
    INTERESTED = "interested"
    APPLIED = "applied"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"


class TrackedApplication(BaseModel):
    id: UUID
    job_id: UUID | None = None
    company_name: str
    title: str
    location_text: str | None = None
    work_mode: str | None = None
    job_url: str | None = None
    salary_text: str | None = None
    stage: ApplicationStage = ApplicationStage.INTERESTED
    notes: str | None = None
    applied_at: datetime | None = None
    interview_at: datetime | None = None
    offer_at: datetime | None = None
    rejected_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TrackerCreateRequest(BaseModel):
    job_id: UUID | None = None
    company_name: str = Field(..., min_length=1, max_length=255)
    title: str = Field(..., min_length=1, max_length=255)
    location_text: str | None = None
    work_mode: str | None = None
    job_url: str | None = None
    salary_text: str | None = None
    stage: ApplicationStage = ApplicationStage.INTERESTED
    notes: str | None = None


class TrackerUpdateRequest(BaseModel):
    company_name: str | None = None
    title: str | None = None
    location_text: str | None = None
    work_mode: str | None = None
    job_url: str | None = None
    salary_text: str | None = None
    stage: ApplicationStage | None = None
    notes: str | None = None
    applied_at: datetime | None = None
    interview_at: datetime | None = None
    offer_at: datetime | None = None
    rejected_at: datetime | None = None


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
