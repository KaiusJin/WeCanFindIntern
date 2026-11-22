"""Sweep cadence: full sweep first, then rolling windows."""

from cloud.planning import sweep


def test_collection_window_starts_full_then_refreshes_every_ten() -> None:
    assert sweep.collection_hours_window(0, recent_hours=48, full_sweep_every=10) is None
    assert sweep.collection_hours_window(1, recent_hours=48, full_sweep_every=10) == 48
    assert sweep.collection_hours_window(9, recent_hours=48, full_sweep_every=10) == 48
    assert sweep.collection_hours_window(10, recent_hours=48, full_sweep_every=10) is None
