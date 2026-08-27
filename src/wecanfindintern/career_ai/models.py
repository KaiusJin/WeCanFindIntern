"""Career AI domain and API schemas for WeCanFindIntern."""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


# --- Profile Models ---

class UserProfile(BaseModel):
    full_name: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    address: str = ""


# --- ATS Resume Review Models ---

class AtsReviewRequest(BaseModel):
    resume_text: str = Field(description="Extracted resume text")
    job_description: str = Field(description="Target job description")
    provider: Literal["Gemini", "OpenAI"] = "Gemini"
    model_name: str | None = None
    api_key: str | None = None


class AtsReviewResponse(BaseModel):
    ok: bool
    score: int = Field(default=0, ge=0, le=100)
    level: str = "中匹配"
    summary: str = ""
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    error: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)


# --- Interview Coach Models ---

class InterviewQuestionsRequest(BaseModel):
    job_description: str
    provider: Literal["Gemini", "OpenAI"] = "Gemini"
    model_name: str | None = None
    api_key: str | None = None


class InterviewQuestionItem(BaseModel):
    id: int
    category: Literal["icebreaker", "behavioral", "situational"]
    category_label: str
    question: str


class InterviewQuestionsResponse(BaseModel):
    ok: bool
    questions: list[InterviewQuestionItem] = Field(default_factory=list)
    error: str | None = None


class TimelineEvent(BaseModel):
    timestamp: str
    type: Literal["Visual", "Audio", "Content", "General"] = "General"
    observation: str


class InterviewAnalyzeResponse(BaseModel):
    ok: bool
    score: int = Field(default=0, ge=0, le=100)
    summary: str = ""
    star_feedback: str | None = None
    timeline: list[TimelineEvent] = Field(default_factory=list)
    advice: list[str] = Field(default_factory=list)
    error: str | None = None


# --- Cover Letter Models ---

class CoverLetterRequest(BaseModel):
    resume_text: str
    job_description: str
    date_str: str = "[Date]"
    user_info: UserProfile = Field(default_factory=UserProfile)
    provider: Literal["Gemini", "OpenAI"] = "Gemini"
    model_name: str | None = None
    api_key: str | None = None


class CoverLetterResponse(BaseModel):
    ok: bool
    text: str = ""
    error: str | None = None
    hr_info: dict[str, str] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)


class CoverLetterExportRequest(BaseModel):
    body: str
    user_info: UserProfile = Field(default_factory=UserProfile)
    date_str: str = "[Date]"
    format: Literal["docx", "pdf", "tex"] = "docx"
