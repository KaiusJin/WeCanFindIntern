"""Pydantic models for mock interview coaching and answer analysis."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class InterviewQuestionsRequest(BaseModel):
    job_description: str
    provider: Literal["Gemini", "OpenAI", "DeepSeek", "GLM", "Qwen", "Ollama"] = "Gemini"
    model_name: str | None = None
    api_key: str | None = None
    api_base: str | None = None


class InterviewSessionCreateRequest(BaseModel):
    """Create a practice session: generate questions and persist them."""

    job_description: str = Field(..., min_length=1, max_length=20000)
    provider: Literal["Gemini", "OpenAI", "DeepSeek", "GLM", "Qwen", "Ollama"] = "Gemini"
    model_name: str | None = None
    api_key: str | None = None
    api_base: str | None = None


class InterviewSessionSummary(BaseModel):
    id: UUID
    provider: str
    model_name: str
    question_count: int
    answer_count: int
    avg_score: int
    created_at: datetime
    last_practiced_at: datetime | None = None


class InterviewSessionDetail(BaseModel):
    id: UUID
    job_description: str
    provider: str
    model_name: str
    questions: list[dict[str, Any]] = Field(default_factory=list)
    answers: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class InterviewQuestionItem(BaseModel):
    id: int
    category: str
    category_label: str
    question: str
    eval_criteria: list[str] = Field(default_factory=list)


class InterviewQuestionsResponse(BaseModel):
    ok: bool
    questions: list[InterviewQuestionItem] = Field(default_factory=list)
    error: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)


class TimelineEvent(BaseModel):
    timestamp: str = ""
    type: str = ""
    observation: str = ""


class InterviewAnalyzeResponse(BaseModel):
    ok: bool
    score: int = 0
    summary: str = ""
    star_feedback: str = ""
    timeline: list[TimelineEvent] = Field(default_factory=list)
    advice: list[str] = Field(default_factory=list)
    transcript: str = ""
    transcript_language: str = ""
    answer_duration_seconds: float = 0.0
    error: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
