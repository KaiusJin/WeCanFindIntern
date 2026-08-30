"""Pydantic contracts for transparent ATS-style diagnostics."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DiagnosticStatus = Literal["pass", "warning", "fail", "unavailable"]
MatchStatus = Literal["matched", "partial", "missing", "unknown"]


class ScoreBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    label: str
    earned: float = Field(ge=0)
    maximum: float = Field(ge=0)
    status: DiagnosticStatus
    evidence: list[str] = Field(default_factory=list)


class ParsingReadinessResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=0, le=100)
    level: str
    confidence: Literal["high", "medium", "limited"]
    mode: Literal["pdf_layout", "text_only"]
    summary: str
    breakdown: list[ScoreBreakdown] = Field(default_factory=list)
    parsed_sections: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class MatchEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: str
    requirement_type: Literal[
        "required_skill",
        "preferred_skill",
        "experience",
        "education",
        "role_alignment",
        "semantic_relevance",
    ]
    status: MatchStatus
    job_evidence: str | None = None
    resume_evidence: str | None = None


class JobMatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: int | None = Field(default=None, ge=0, le=100)
    insufficient_evidence: bool = False
    level: str
    confidence: Literal["high", "medium", "limited"]
    summary: str
    breakdown: list[ScoreBreakdown] = Field(default_factory=list)
    matched: list[MatchEvidence] = Field(default_factory=list)
    partial_matches: list[MatchEvidence] = Field(default_factory=list)
    missing: list[MatchEvidence] = Field(default_factory=list)
    unknowns: list[MatchEvidence] = Field(default_factory=list)
    eligibility_flags: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    scoring_version: str = "ats-match.v1"


class AtsReviewRequest(BaseModel):
    resume_text: str = Field(min_length=1, max_length=120_000)
    job_description: str = Field(min_length=1, max_length=120_000)
    # Retained for request compatibility. Deterministic scoring never asks the
    # selected model to invent or adjust the score.
    provider: Literal["Gemini", "OpenAI", "DeepSeek", "GLM", "Qwen", "Ollama"] = (
        "Gemini"
    )
    model_name: str | None = None
    api_key: str | None = None
    api_base: str | None = None


class AtsReviewResponse(BaseModel):
    ok: bool
    parsing_readiness: ParsingReadinessResult | None = None
    job_match: JobMatchResult | None = None
    error: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
