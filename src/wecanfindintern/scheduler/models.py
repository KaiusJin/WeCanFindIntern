"""Scheduler database records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class CollectionPlan:
    id: int
    name: str
    sites: list[str]
    query: dict[str, Any]
    interval_seconds: int
    page_size: int
    max_results_per_source: int
    max_attempts: int
    active_run_id: int | None


@dataclass(frozen=True, slots=True)
class CollectionCheckpoint:
    plan_id: int
    source: str
    run_id: int
    offset: int
    status: int
    attempts: int
    pages_completed: int
    records_seen: int
    next_retry_at: datetime | None

    @property
    def terminal(self) -> bool:
        return self.status in {3, 4}
