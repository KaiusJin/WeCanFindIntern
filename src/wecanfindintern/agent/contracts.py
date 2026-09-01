"""Dependency and error contracts shared by Agent feature modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wecanfindintern.agent.recommend.embeddings import EmbeddingConfig
from wecanfindintern.agent.recommend.repository import RecommendationRepository
from wecanfindintern.db.read_repository import JobReadRepository
from wecanfindintern.profile.repository import ProfileRepository
from wecanfindintern.tracker.repository import TrackerRepository


class ToolError(RuntimeError):
    """A tool-level failure with a stable error type for UI and audit records."""

    def __init__(self, error_type: str, message: str) -> None:
        self.error_type = error_type
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class LlmConfig:
    provider: str
    model_name: str
    api_key: str
    api_base: str | None = None
    timeout_seconds: float = 15.0


@dataclass(slots=True)
class AgentDeps:
    job_repo: JobReadRepository
    tracker_repo: TrackerRepository
    profile_repo: ProfileRepository
    waterlooworks: Any
    llm_config: LlmConfig | None = None
    embedding_config: EmbeddingConfig | None = None
    memory: Any = None
    recommendation_repo: RecommendationRepository | None = None
