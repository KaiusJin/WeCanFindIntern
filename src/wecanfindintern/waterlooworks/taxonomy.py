"""Canonical mappings between WaterlooWorks boards and job taxonomy fields."""

from __future__ import annotations

WATERLOOWORKS_BOARD_EMPLOYMENT_EVIDENCE: dict[str, tuple[str, ...]] = {
    # Board names express opportunity type, not employment arrangement.  Only
    # retain values for boards that provide actual schedule/employment evidence.
    "full_cycle": (),
    "employer_student_direct": (),
    "graduating": ("full_time",),
    "contract": ("contract",),
    "campus": ("part_time",),
}

WATERLOOWORKS_BOARD_LABELS: dict[str, str] = {
    "full_cycle": "Co-op: Full-Cycle",
    "employer_student_direct": "Employer-Student Direct",
    "graduating": "Graduating jobs",
    "contract": "Contract jobs",
    "campus": "Campus jobs",
}

WATERLOOWORKS_BOARD_OPPORTUNITY_TYPES: dict[str, str] = {
    "full_cycle": "co_op",
    "employer_student_direct": "co_op",
    "graduating": "new_grad",
    "contract": "contract",
}


def infer_waterloo_opportunity_type(boards: list[str] | None) -> str | None:
    """Infer one canonical opportunity type from source-board membership."""

    normalized = {
        value.strip().lower() for value in boards or [] if value and value.strip()
    }
    for board in WATERLOOWORKS_BOARD_OPPORTUNITY_TYPES:
        if board in normalized:
            return WATERLOOWORKS_BOARD_OPPORTUNITY_TYPES[board]
    return None


def resolve_waterloo_opportunity_type(
    stored_value: str | None, boards: list[str] | None
) -> str | None:
    """Apply board semantics before legacy stored values without rewriting rows."""

    return infer_waterloo_opportunity_type(boards) or (
        stored_value.strip().lower() if stored_value and stored_value.strip() else None
    )


def boards_for_opportunity_types(opportunity_types: list[str]) -> list[str]:
    """Resolve canonical opportunity filters to source-board fallbacks."""

    requested = {value.strip().lower() for value in opportunity_types if value.strip()}
    return sorted(
        board
        for board, opportunity_type in WATERLOOWORKS_BOARD_OPPORTUNITY_TYPES.items()
        if opportunity_type in requested
    )
