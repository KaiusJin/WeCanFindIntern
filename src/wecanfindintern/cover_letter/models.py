"""Pydantic models for cover letter generation and document export."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class UserProfile(BaseModel):
    full_name: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    address: str = ""


class CoverLetterRequest(BaseModel):
    resume_text: str
    job_description: str
    job_title: str = ""
    company_name: str = ""
    company_location: str = ""
    hiring_manager: str = ""
    company_information: str = ""
    date_str: str = "[Date]"
    user_info: UserProfile = Field(default_factory=UserProfile)
    provider: Literal["Gemini", "OpenAI", "DeepSeek"] = "Gemini"
    model_name: str | None = None
    api_key: str | None = None


class CoverLetterResponse(BaseModel):
    ok: bool
    text: str = ""
    error: str | None = None
    hr_info: dict[str, str] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)
    review_approved: bool | None = None
    review_issues: list[str] = Field(default_factory=list)
    review_unsupported_claims: list[str] = Field(default_factory=list)
    review_summary: str = ""
    review_attempts: int = Field(default=0, ge=0, le=5)


class CoverLetterExportRequest(BaseModel):
    body: str
    user_info: UserProfile = Field(default_factory=UserProfile)
    date_str: str = "[Date]"
    format: Literal["docx", "pdf"] = "docx"
