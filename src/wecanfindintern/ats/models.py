"""Pydantic models for ATS resume evaluation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AtsReviewRequest(BaseModel):
    resume_text: str
    job_description: str
    provider: Literal["Gemini", "OpenAI", "DeepSeek", "GLM", "Qwen", "Ollama"] = "Gemini"
    model_name: str | None = None
    api_key: str | None = None
    api_base: str | None = None


class AtsReviewResponse(BaseModel):
    ok: bool
    score: int = 0
    level: str = "Unrated"
    summary: str = ""
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    error: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
