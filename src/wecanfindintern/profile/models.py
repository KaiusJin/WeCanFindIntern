"""Public models for the candidate profile workspace."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ProfileBasics(BaseModel):
    full_name: str = Field(default="", max_length=200)
    preferred_name: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=80)
    city: str | None = Field(default=None, max_length=120)
    region: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=120)
    linkedin_url: str | None = Field(default=None, max_length=1000)
    github_url: str | None = Field(default=None, max_length=1000)
    portfolio_url: str | None = Field(default=None, max_length=1000)


class ParsedEntry(BaseModel):
    id: UUID | None = None
    evidence: str | None = Field(default=None, max_length=12000)
    confidence: float | None = Field(default=None, ge=0, le=1)


class EducationEntry(ParsedEntry):
    institution: str = Field(default="", max_length=300)
    degree: str | None = Field(default=None, max_length=200)
    major: str | None = Field(default=None, max_length=200)
    specialization: str | None = Field(default=None, max_length=200)
    minor: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    start_date_text: str | None = Field(default=None, max_length=80)
    graduation_date_text: str | None = Field(default=None, max_length=80)
    graduation_year: int | None = Field(default=None, ge=1900, le=2200)
    expected_graduation: bool = False
    status: Literal["studying", "graduated", "paused", "incomplete", "unknown"] = "unknown"
    gpa: str | None = Field(default=None, max_length=40)
    coursework: list[str] = Field(default_factory=list, max_length=100)


class WorkEntry(ParsedEntry):
    company: str = Field(default="", max_length=300)
    title: str | None = Field(default=None, max_length=240)
    location: str | None = Field(default=None, max_length=200)
    employment_type: str | None = Field(default=None, max_length=80)
    start_date_text: str | None = Field(default=None, max_length=80)
    end_date_text: str | None = Field(default=None, max_length=80)
    is_current: bool = False
    description: str | None = Field(default=None, max_length=6000)
    skills: list[str] = Field(default_factory=list, max_length=200)


class ProjectEntry(ParsedEntry):
    name: str = Field(default="", max_length=300)
    description: str | None = Field(default=None, max_length=6000)
    start_date_text: str | None = Field(default=None, max_length=80)
    end_date_text: str | None = Field(default=None, max_length=80)
    project_url: str | None = Field(default=None, max_length=1000)
    github_url: str | None = Field(default=None, max_length=1000)
    skills: list[str] = Field(default_factory=list, max_length=200)


class SkillEntry(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def strip_skill_name(cls, value: str) -> str:
        return value.strip()


class CertificationEntry(ParsedEntry):
    name: str = Field(default="", max_length=300)
    issuer: str | None = Field(default=None, max_length=240)
    issue_date_text: str | None = Field(default=None, max_length=80)
    expiry_date_text: str | None = Field(default=None, max_length=80)
    credential_id: str | None = Field(default=None, max_length=200)
    credential_url: str | None = Field(default=None, max_length=1000)


class LanguageEntry(ParsedEntry):
    name: str = Field(default="", max_length=120)
    proficiency: str | None = Field(default=None, max_length=120)


class AwardEntry(ParsedEntry):
    title: str = Field(default="", max_length=300)
    issuer: str | None = Field(default=None, max_length=240)
    date_text: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=4000)


class ProfilePayload(BaseModel):
    schema_version: Literal["profile.v1"] = "profile.v1"
    basics: ProfileBasics = Field(default_factory=ProfileBasics)
    education: list[EducationEntry] = Field(default_factory=list, max_length=50)
    work_experience: list[WorkEntry] = Field(default_factory=list, max_length=100)
    projects: list[ProjectEntry] = Field(default_factory=list, max_length=100)
    skills: list[SkillEntry] = Field(default_factory=list, max_length=500)
    certifications: list[CertificationEntry] = Field(default_factory=list, max_length=100)
    languages: list[LanguageEntry] = Field(default_factory=list, max_length=100)
    awards: list[AwardEntry] = Field(default_factory=list, max_length=100)


class UserProfile(ProfilePayload):
    id: UUID
    completion_percent: int = Field(default=0, ge=0, le=100)
    created_at: datetime
    updated_at: datetime


class ResumeDocumentSummary(BaseModel):
    id: UUID
    filename: str
    source_type: Literal["pdf", "latex"]
    media_type: str
    size_bytes: int
    sha256: str
    parser_version: str
    status: Literal["draft", "confirmed", "failed"]
    created_at: datetime
    confirmed_at: datetime | None = None


class ResumeImportResult(BaseModel):
    import_id: UUID
    resume: ResumeDocumentSummary
    draft: ProfilePayload
    extracted_text: str
    warnings: list[str] = Field(default_factory=list)
