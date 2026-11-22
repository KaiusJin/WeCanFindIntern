"""Contracts for WaterlooWorks use cases; implemented by infrastructure adapters."""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

ApplicationSync = Callable[[dict[str, Any]], Awaitable[object]]


class WaterlooWorksBoardUnavailable(RuntimeError):
    """The current account cannot search this WaterlooWorks board."""


class BrowserSession(Protocol):
    async def launch(self) -> None: ...
    async def close(self) -> None: ...
    async def load_existing_debug_port(self) -> bool: ...
    async def find_target(self, url_contains: str) -> dict[str, Any] | None: ...
    async def activate_or_create_target(self, target: dict[str, Any] | None) -> None: ...
    async def target_window_state(self, target: dict[str, Any]) -> str | None: ...
    async def readiness(self, target: dict[str, Any]) -> dict[str, Any]: ...
    async def is_authenticated(self, target: dict[str, Any]) -> bool: ...


class JobReader(Protocol):
    async def read_board(
        self,
        target: dict[str, Any],
        *,
        board_name: str,
        board_url: str,
        known_job_ids: set[str],
        on_display_mode: Callable[[str], None],
    ) -> list[dict[str, Any]]: ...


class ApplicationReader(Protocol):
    async def read_applications(
        self,
        target: dict[str, Any],
        *,
        on_reading: Callable[[], None],
    ) -> list[dict[str, Any]]: ...


class JobRepository(Protocol):
    """Synchronous local storage operations; use cases dispatch them to threads."""

    def start_run(self, boards: list[dict[str, Any]]) -> str: ...

    def mark_board_collecting(self, run_id: str, board: str) -> None: ...

    def mark_board_failed(self, run_id: str, board: str, error: str) -> None: ...

    def mark_board_skipped(self, run_id: str, board: str, reason: str) -> None: ...

    def store_board_postings(
        self,
        run_id: str,
        board: str,
        raw_jobs: list[dict[str, Any]],
    ) -> tuple[Counter[str], list[str]]: ...

    def store_applications(
        self, raw_applications: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, int], list[str]]: ...

    def count_applications(self) -> int: ...

    def count_jobs(self) -> int: ...

    def known_job_ids(self) -> set[str]: ...

    def finish_run(self, run_id: str, error_summary: str | None = None) -> dict[str, Any]: ...

    def fail_run(self, run_id: str, error: str) -> None: ...

    def list_jobs(
        self,
        *,
        board: str | None = None,
        boards: list[str] | None = None,
        query: str | None = None,
        location: str | None = None,
        company: str | None = None,
        skill: str | None = None,
        category: str | None = None,
        city: str | None = None,
        region: str | None = None,
        country: str | None = None,
        work_modes: list[str] | None = None,
        opportunity_types: list[str] | None = None,
        posted_after: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        include_description: bool = False,
    ) -> dict[str, Any]: ...

    def get_job(self, source_job_id: str) -> dict[str, Any] | None: ...

    def count_source_ids(self, source_job_ids: list[str]) -> int: ...

    def latest_run(self) -> dict[str, Any] | None: ...

    def latest_application_sync_at(self) -> str | None: ...

    def latest_job_update_at(self) -> str | None: ...
