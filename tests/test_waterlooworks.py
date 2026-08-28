"""Unit tests for the WaterlooWorks module split."""

from __future__ import annotations

from wecanfindintern.waterlooworks.collector import WaterlooWorksCollector
from wecanfindintern.waterlooworks.state import (
    WaterlooWorksSnapshot,
    initial_board_states,
)


def test_initial_board_states_covers_all_boards():
    boards = initial_board_states()
    assert [board["name"] for board in boards] == [
        "full_cycle",
        "employer_student_direct",
        "graduating",
        "contract",
        "campus",
    ]


def test_snapshot_payload_round_trip():
    snapshot = WaterlooWorksSnapshot()
    payload = snapshot.payload()
    assert payload["status"] == "idle"
    assert len(payload["boards"]) == 5


def test_collector_board_state_lookup():
    snapshot = WaterlooWorksSnapshot()
    collector = WaterlooWorksCollector(
        session=None,  # type: ignore[arg-type]
        repository=None,  # type: ignore[arg-type]
        snapshot=snapshot,
    )
    state = collector._board_state("full_cycle")
    assert state["label"] == "Co-op: Full-Cycle"
