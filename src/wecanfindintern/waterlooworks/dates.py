"""WaterlooWorks date boundaries.

Posting deadlines are displayed by WaterlooWorks in Toronto local time.  The API
keeps that source text intact; these helpers exist only for consumers that need a
typed calendar date or an absolute submitted-at timestamp.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo


def parse_waterlooworks_datetime(value: str | None) -> datetime | None:
    """Convert a Toronto-local timestamp to an absolute UTC timestamp."""

    text = " ".join((value or "").split())
    if not text:
        return None
    for pattern in ("%b %d, %Y %I:%M %p", "%B %d, %Y %I:%M %p"):
        try:
            local = datetime.strptime(text, pattern).replace(
                tzinfo=ZoneInfo("America/Toronto")
            )
            return local.astimezone(UTC)
        except ValueError:
            continue
    return None


def parse_waterlooworks_date(value: str | None) -> date | None:
    """Read the Toronto calendar date without converting through UTC."""

    text = " ".join((value or "").split())
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    for pattern in (
        "%b %d, %Y %I:%M %p",
        "%B %d, %Y %I:%M %p",
        "%b %d, %Y",
        "%B %d, %Y",
    ):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None
