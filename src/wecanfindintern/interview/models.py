"""Pydantic models for mock interview coaching and answer analysis."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class InterviewQuestionsRequest(BaseModel):
    job_description: str
    provider: Literal["Gemini", "OpenAI", "DeepSeek"] = "Gemini"
    model_name: str | None = None
    api_key: str | None = None


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
    error: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
