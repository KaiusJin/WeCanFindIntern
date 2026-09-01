"""Small provider text-normalization helpers shared by extraction and storage."""

from __future__ import annotations

from typing import Any


def clean_waterlooworks_text(value: Any) -> str:
    """Collapse provider whitespace while preserving the source wording."""

    return " ".join(str(value or "").split())


def optional_waterlooworks_text(value: Any) -> str | None:
    """Return normalized provider text, or ``None`` when it is empty."""

    return clean_waterlooworks_text(value) or None
