"""Shared decoding for rows read from the WaterlooWorks SQLite library."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from wecanfindintern.domain.salary import format_salary_text
from wecanfindintern.waterlooworks.taxonomy import (
    WATERLOOWORKS_BOARD_LABELS,
    resolve_waterloo_opportunity_type,
)
from wecanfindintern.waterlooworks.text import optional_waterlooworks_text

JSON_LIST_FIELDS = (
    "skill_tags",
    "schedule_types",
    "job_subcategories",
    "requirement_tags",
    "display_tags",
)


def decode_waterlooworks_job(
    row: Mapping[str, Any], *, include_description: bool = True
) -> dict[str, Any]:
    """Return the canonical read-side dictionary for one stored job row."""

    item = dict(row)
    boards = item.get("boards")
    if isinstance(boards, str):
        try:
            boards = json.loads(boards or "[]")
        except (TypeError, ValueError):
            boards = []
    item["boards"] = sorted(set(boards or []))
    item["board_labels"] = {
        board: WATERLOOWORKS_BOARD_LABELS.get(board, board.replace("_", " ").title())
        for board in item["boards"]
    }
    item["opportunity_type"] = resolve_waterloo_opportunity_type(
        item.get("opportunity_type"), item["boards"]
    )
    # This is already Toronto-local display text. Keep it verbatim instead of
    # converting through UTC or discarding its time-of-day.
    item["application_deadline"] = optional_waterlooworks_text(
        item.get("application_deadline")
    )
    alias_map = {
        "term": "application_term",
        "app_status": "application_status",
        "job_status": "application_job_status",
        "openings": "application_openings",
        "submitted_at": "application_submitted_at",
        "submitted_by": "application_submitted_by",
    }
    for stored_name, public_name in alias_map.items():
        if public_name not in item and stored_name in item:
            item[public_name] = item.get(stored_name)
        item.pop(stored_name, None)
    for key in JSON_LIST_FIELDS:
        value = item.get(key)
        if isinstance(value, str):
            try:
                value = json.loads(value or "[]")
            except (TypeError, ValueError):
                value = []
        item[key] = list(value or [])
    for internal_key in (
        "raw_payload",
        "payload_hash",
        "pagination_rowid",
        "salary_text",
        "updated_at",
    ):
        item.pop(internal_key, None)
    if not include_description:
        item.pop("description", None)
    return item


def waterlooworks_salary_text(job: Mapping[str, Any]) -> str | None:
    """Format one stored WaterlooWorks salary for text-only consumers."""

    return format_salary_text(
        job.get("salary_min"),
        job.get("salary_max"),
        currency=job.get("salary_currency"),
        interval=job.get("salary_interval"),
    )


def waterlooworks_current_application_deadline(job: Mapping[str, Any]) -> str | None:
    """Prefer the independently refreshed submitted-application deadline."""

    return job.get("submitted_application_deadline") or job.get("application_deadline")
