"""Shared connection state and the single background-task lifecycle."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from wecanfindintern.waterlooworks.application.ports import BrowserSession, JobRepository
from wecanfindintern.waterlooworks.application.state import (
    WaterlooWorksSnapshot,
    initial_board_states,
)


class WaterlooWorksRuntime:
    """Share one browser, repository, snapshot, and lock across feature use cases."""

    def __init__(self, *, session: BrowserSession, repository: JobRepository) -> None:
        self.session = session
        self.repository = repository
        self.snapshot = self._snapshot_from_latest_run()
        self.snapshot.unique_job_count = self.repository.count_jobs()
        self.snapshot.application_count = self.repository.count_applications()
        self.snapshot.last_tracker_update_at = self.repository.latest_application_sync_at()
        self.snapshot.last_job_update_at = self.repository.latest_job_update_at()
        self.active_task: asyncio.Task[None] | None = None
        self.minimize_attempted_for_login = False
        self.lock = asyncio.Lock()

    def _snapshot_from_latest_run(self) -> WaterlooWorksSnapshot:
        latest = self.repository.latest_run()
        if not latest:
            return WaterlooWorksSnapshot()
        board_states = initial_board_states()
        saved_boards = {board["board"]: board for board in latest["boards"]}
        for state in board_states:
            saved = saved_boards.get(state["name"])
            if not saved:
                continue
            for key in (
                "status",
                "discovered_count",
                "posting_failed_count",
                "error",
            ):
                state[key] = saved[key]
            state["posting_inserted_count"] = int(saved["posting_success_count"])
            state["posting_known_count"] = max(
                int(saved["discovered_count"])
                - int(state["posting_inserted_count"])
                - int(saved["posting_failed_count"]),
                0,
            )
        posting_known_count = sum(int(board["posting_known_count"]) for board in board_states)
        return WaterlooWorksSnapshot(
            status=latest["status"],
            message="",
            unique_job_count=latest["unique_job_count"],
            posting_inserted_count=latest["posting_success_count"],
            posting_known_count=posting_known_count,
            posting_failed_count=latest["posting_failed_count"],
            board_failed_count=latest["board_failed_count"],
            started_at=latest["started_at"],
            finished_at=latest["finished_at"],
            last_job_update_at=self.repository.latest_job_update_at(),
            run_id=latest["id"],
            boards=board_states,
        )

    async def close(self) -> None:
        if self.active_task and not self.active_task.done():
            self.active_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.active_task
        await self.session.close()
        self.minimize_attempted_for_login = False
