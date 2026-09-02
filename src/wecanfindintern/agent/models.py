"""Pydantic contracts for the AI Agent workspace."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from wecanfindintern.domain.classification import OpportunityType
from wecanfindintern.llm.providers import ProviderName
from wecanfindintern.tracker.models import ApplicationStage


class AgentSession(BaseModel):
    id: UUID
    title: str = "New conversation"
    created_at: datetime
    updated_at: datetime


AgentRole = Literal["user", "assistant"]


class AgentMessage(BaseModel):
    id: UUID
    session_id: UUID
    role: AgentRole
    content: str
    created_at: datetime


class AgentToolCall(BaseModel):
    id: UUID
    session_id: UUID
    message_id: UUID | None = None
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: Literal["succeeded", "failed", "awaiting_approval"] = "succeeded"
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentApproval(BaseModel):
    id: UUID
    session_id: UUID
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    preview: dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "approved", "denied"] = "pending"
    created_at: datetime
    decided_at: datetime | None = None


class AgentSessionResponse(BaseModel):
    session: AgentSession


class AgentJobContext(BaseModel):
    id: str = Field(min_length=1, max_length=255)
    source: Literal["public", "waterloo_work"] = "public"
    title: str | None = Field(default=None, max_length=500)
    company: str | None = Field(default=None, max_length=500)
    location: str | None = Field(default=None, max_length=500)
    work_mode: str | None = Field(default=None, max_length=40)
    application_deadline: str | None = Field(default=None, max_length=120)
    jd: str | None = Field(default=None, max_length=30_000)


class AgentContext(BaseModel):
    """Jobs explicitly attached to the current Agent message.

    ``job`` is retained for older clients. New clients send ``jobs`` so one
    turn can carry several roles into comparison and batch tools.
    """

    jobs: list[AgentJobContext] = Field(default_factory=list, max_length=5)
    job: AgentJobContext | None = None

    @model_validator(mode="after")
    def require_job_context(self) -> AgentContext:
        if not self.jobs and self.job is None:
            raise ValueError("At least one attached job is required.")
        return self


class AgentMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)
    provider: ProviderName | None = None
    model_name: str | None = None
    api_key: str | None = None
    api_base: str | None = None
    embedding_provider: Literal["OpenAI", "Gemini", "Ollama"] | None = None
    embedding_model: str | None = None
    embedding_dimensions: int | None = Field(default=None, ge=1, le=4096)
    embedding_api_key: str | None = None
    embedding_api_base: str | None = None
    context: AgentContext | None = None


class AgentEmbeddingConfigRequest(BaseModel):
    provider: Literal["OpenAI", "Gemini", "Ollama"]
    model: str = Field(min_length=1, max_length=200)
    dimensions: int = Field(default=768, ge=1, le=4096)
    api_key: str | None = None
    api_base: str | None = Field(default=None, max_length=500)


class AgentDecisionRequest(BaseModel):
    approved: bool


class AgentTurnResult(BaseModel):
    """Everything a single user turn produced, for the UI to render."""

    message: AgentMessage
    tool_calls: list[AgentToolCall] = Field(default_factory=list)
    pending_approval: AgentApproval | None = None
    session: AgentSession


class AgentApprovalDecisionResult(BaseModel):
    message: AgentMessage
    tool_calls: list[AgentToolCall] = Field(default_factory=list)
    approval: AgentApproval
    session: AgentSession


class JobReference(BaseModel):
    """Unified job reference: public UUID or WaterlooWorks Job ID."""

    job_id: str = Field(..., min_length=1, max_length=255)
    source: Literal["public", "waterloo_work"] = "public"

    @field_validator("job_id")
    @classmethod
    def strip_job_id(cls, value: str) -> str:
        return value.strip()

    def display(self) -> str:
        prefix = "public" if self.source == "public" else "ww"
        return f"{prefix}:{self.job_id}"


class SearchJobsArgs(BaseModel):
    query: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=160)
    city: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    region: str | None = Field(default=None, max_length=32)
    skill: str | None = Field(default=None, max_length=80)
    category: str | None = Field(default=None, max_length=60)
    work_modes: list[Literal["remote", "hybrid", "onsite"]] = Field(
        default_factory=list, max_length=3
    )
    opportunity_types: list[OpportunityType] = Field(default_factory=list, max_length=10)
    recruiting_terms: list[str] = Field(default_factory=list, max_length=10)
    posted_after: date | None = None
    source: Literal["all", "public", "waterloo_work"] = "all"
    cursor: str | None = Field(default=None, max_length=256)
    public_cursor: str | None = Field(default=None, max_length=256)
    waterloo_cursor: str | None = Field(default=None, max_length=256)
    limit: int = Field(default=10, ge=1, le=50)


class GetJobDetailsArgs(BaseModel):
    job_id: str = Field(..., min_length=1, max_length=255)
    source: Literal["public", "waterloo_work"] = "public"


class AnalyseJobArgs(BaseModel):
    """Analyse one explicit job against the confirmed Profile and preferences."""

    job: JobReference
    response_language: Literal["en", "zh"] = "en"


class CompareJobsArgs(BaseModel):
    """Compare explicit jobs against the confirmed Profile and preferences."""

    jobs: list[JobReference] = Field(..., min_length=2, max_length=5)


class ListTrackerArgs(BaseModel):
    query: str | None = Field(default=None, max_length=200)
    stage: ApplicationStage | None = None
    limit: int = Field(default=50, ge=1, le=100)


class AddIntoTrackerArgs(BaseModel):
    jobs: list[JobReference] = Field(..., min_length=1, max_length=25)


class UpdateTrackerStageArgs(BaseModel):
    application_ids: list[str] = Field(default_factory=list, max_length=100)
    job_references: list[JobReference] = Field(default_factory=list, max_length=25)
    stage: ApplicationStage = ApplicationStage.APPLIED


class RemoveTrackerArgs(BaseModel):
    """Targets that can be removed from the Tracker after approval."""

    application_ids: list[str] = Field(default_factory=list, max_length=100)
    job_references: list[JobReference] = Field(default_factory=list, max_length=25)


# Kept as an import-compatible name for callers that used the old Interested-only
# tool contract. The Agent catalog exposes RemoveTrackerArgs instead.
RemoveInterestedArgs = RemoveTrackerArgs


class ProposeProfileUpdateArgs(BaseModel):
    request: str = Field(..., min_length=1, max_length=2000)


class UpdateProfileArgs(BaseModel):
    payload: dict[str, Any]


class RecommendJobsArgs(BaseModel):
    limit: int = Field(default=10, ge=1, le=20)
    source: Literal["all", "public", "waterloo_work"] = "all"
    target_roles: list[str] = Field(default_factory=list, max_length=10)
    locations: list[str] = Field(default_factory=list, max_length=10)
    work_modes: list[Literal["remote", "hybrid", "onsite"]] = Field(
        default_factory=list, max_length=3
    )
    opportunity_types: list[OpportunityType] = Field(default_factory=list, max_length=10)
    use_semantic_retrieval: bool = True
    use_llm_rerank: bool = False
    exclude_tracked: bool = True


class GenerateInterviewQuestionsArgs(BaseModel):
    """Generate mock interview questions for one job or a raw description."""

    job_id: str | None = Field(default=None, max_length=255)
    source: Literal["public", "waterloo_work"] = "public"
    job_description: str | None = Field(default=None, max_length=8000)
