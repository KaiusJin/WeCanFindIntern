"""Sweep cadence: a full provider sweep every N campaigns, rolling windows in between."""

from __future__ import annotations


def collection_hours_window(
    completed_campaigns: int,
    *,
    recent_hours: int,
    full_sweep_every: int,
) -> int | None:
    """Return None for full sweeps, otherwise the rolling provider age window."""

    if completed_campaigns < 0:
        raise ValueError("completed_campaigns cannot be negative")
    if recent_hours < 1:
        raise ValueError("recent_hours must be positive")
    if full_sweep_every < 1:
        raise ValueError("full_sweep_every must be positive")
    return None if completed_campaigns % full_sweep_every == 0 else recent_hours
