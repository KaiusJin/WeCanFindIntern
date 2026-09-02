"""Shared WaterlooWorks snapshot state used by service and collector."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from wecanfindintern.waterlooworks.config import WATERLOOWORKS_BOARDS
from wecanfindintern.waterlooworks.taxonomy import WATERLOOWORKS_BOARD_LABELS


def initial_board_states() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "label": WATERLOOWORKS_BOARD_LABELS[name],
            "url": url,
            "status": "pending",
            "discovered_count": 0,
            "posting_inserted_count": 0,
            "posting_known_count": 0,
            "posting_failed_count": 0,
            "display_mode": None,
            "error": None,
        }
        for name, url in WATERLOOWORKS_BOARDS
    ]


@dataclass(slots=True)
class WaterlooWorksSnapshot:
    status: str = "idle"
    message: str = "Open a dedicated Chrome window to connect WaterlooWorks."
    browser_open: bool = False
    page_url: str | None = None
    unique_job_count: int = 0
    posting_inserted_count: int = 0
    posting_known_count: int = 0
    posting_failed_count: int = 0
    board_failed_count: int = 0
    application_count: int = 0
    application_new_job_count: int = 0
    tracker_synced_count: int = 0
    application_failed_count: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    last_tracker_update_at: str | None = None
    last_job_update_at: str | None = None
    run_id: str | None = None
    boards: list[dict[str, Any]] = field(default_factory=initial_board_states)

    def payload(self) -> dict[str, Any]:
        return asdict(self)
