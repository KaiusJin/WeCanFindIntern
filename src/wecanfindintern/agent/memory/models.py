"""Data contracts for agent memory."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

MEMORY_TYPES = {
    "USER_PREFERENCE",
    "CAREER_CONTEXT",
    "JOB_TARGET",
    "EXPLICIT_FACT",
    "SKILL_PROFILE",
    "EDUCATION_PROFILE",
    "WORK_EXPERIENCE",
    "APPLICATION_PLAN",
}

MEMORY_STATUS_ACTIVE = "ACTIVE"
MEMORY_STATUS_SUPERSEDED = "SUPERSEDED"
MEMORY_STATUS_EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class MemoryMessage:
    id: UUID
    session_id: UUID
    role: str
    content: str
    token_count: int
    created_at: datetime


@dataclass(frozen=True)
class SessionMemoryState:
    session_id: UUID
    summary_text: str | None
    summary_json: str | None
    summary_version: int
    summary_token_count: int
    summary_covers_through_message_id: UUID | None
    extraction_covers_through_message_id: UUID | None


@dataclass(frozen=True)
class ConversationSummary:
    session_id: UUID
    version: int
    summary_text: str
    summary_json: str
    token_count: int
    covered_message_count: int
    covers_through_message_id: UUID | None
    provider: str
    model: str


@dataclass(frozen=True)
class MemoryRecord:
    id: UUID
    memory_type: str
    content: str
    content_hash: str
    confidence: float
    status: str = MEMORY_STATUS_ACTIVE
    session_id: UUID | None = None
    source_message_id: UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    expires_at: datetime | None = None
    access_count: int = 0
    last_accessed_at: datetime | None = None


@dataclass(frozen=True)
class MemoryCandidate:
    memory_type: str
    content: str
    confidence: float
    source_message_id: UUID | None
    ttl_days: int | None = None


@dataclass(frozen=True)
class RecalledMemory:
    record: MemoryRecord
    score: float


@dataclass(frozen=True)
class WindowSelection:
    window: list[MemoryMessage]
    token_count: int
    clipped_message_ids: list[UUID]
    excluded_message_count: int


@dataclass(frozen=True)
class WorkingContext:
    """Assembled short-term + long-term context for one turn."""

    session_id: UUID
    summary_text: str | None
    window: list[MemoryMessage]
    recalled_memories: list[RecalledMemory]
    preferences: dict[str, str] = field(default_factory=dict)
    window_token_count: int = 0
    summary_token_count: int = 0
    memory_token_count: int = 0
    diagnostics: dict = field(default_factory=dict)

    @property
    def total_token_count(self) -> int:
        return (
            self.window_token_count
            + self.summary_token_count
            + self.memory_token_count
        )


@dataclass(frozen=True)
class MaintenanceReport:
    session_id: UUID
    summarized: bool
    summary_version: int | None
    evicted_message_count: int
    extraction_ran: bool
    candidates_extracted: int
    memories_added: int
    memories_updated: int
    memories_skipped: int
    errors: list[str] = field(default_factory=list)
