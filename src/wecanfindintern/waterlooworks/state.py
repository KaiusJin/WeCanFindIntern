"""Shared WaterlooWorks snapshot state used by service and collector."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from wecanfindintern.waterlooworks.extractor import WATERLOOWORKS_BOARDS


def initial_board_states() -> list[dict[str, Any]]:
    labels = {
        "full_cycle": "Co-op: Full-Cycle",
        "employer_student_direct": "Employer-Student Direct",
        "graduating": "Graduating jobs",
        "contract": "Contract jobs",
        "campus": "Campus jobs",
    }
    return [
        {
            "name": name,
            "label": labels[name],
            "url": url,
            "status": "pending",
            "discovered_count": 0,
            "posting_success_count": 0,
            "posting_failed_count": 0,
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
    posting_success_count: int = 0
    posting_failed_count: int = 0
    board_failed_count: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    run_id: str | None = None
    boards: list[dict[str, Any]] = field(default_factory=initial_board_states)

    def payload(self) -> dict[str, Any]:
        return asdict(self)
