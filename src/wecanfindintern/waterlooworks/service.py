"""Orchestrate the WaterlooWorks browser session and collection lifecycle."""

from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wecanfindintern.waterlooworks.browser import ChromeSession, find_chrome_binary
from wecanfindintern.waterlooworks.collector import WaterlooWorksCollector
from wecanfindintern.waterlooworks.extractor import WATERLOOWORKS_BOARDS
from wecanfindintern.waterlooworks.repository import WaterlooWorksRepository
from wecanfindintern.waterlooworks.state import (
    WaterlooWorksSnapshot,
    initial_board_states,
)


class WaterlooWorksService:
    """Own the Chrome session, the status snapshot, and one collection task."""

    def __init__(self) -> None:
        self.profile_dir = Path(
            os.getenv(
                "WATERLOOWORKS_CHROME_PROFILE",
                str(Path.home() / ".wecanfindintern" / "chrome-waterlooworks"),
            )
        ).expanduser()
        self.start_url = os.getenv("WATERLOOWORKS_URL", WATERLOOWORKS_BOARDS[0][1])
        database_path = Path(
            os.getenv(
                "WATERLOOWORKS_DB_PATH",
                str(Path.home() / ".wecanfindintern" / "waterlooworks.sqlite3"),
            )
        ).expanduser()
        self.repository = WaterlooWorksRepository(database_path)
        self.session = ChromeSession(
            profile_dir=self.profile_dir,
            start_url=self.start_url,
            chrome_binary=os.getenv("WATERLOOWORKS_CHROME_BINARY") or find_chrome_binary(),
        )
        self.snapshot = self._snapshot_from_latest_run()
        self.collect_task: asyncio.Task[None] | None = None
        self._minimize_attempted_for_login = False
        self._lock = asyncio.Lock()

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
                "posting_success_count",
                "posting_failed_count",
                "error",
            ):
                state[key] = saved[key]
        return WaterlooWorksSnapshot(
            status=latest["status"],
            message="",
            unique_job_count=latest["unique_job_count"],
            posting_success_count=latest["posting_success_count"],
            posting_failed_count=latest["posting_failed_count"],
            board_failed_count=latest["board_failed_count"],
            started_at=latest["started_at"],
            finished_at=latest["finished_at"],
            run_id=latest["id"],
            boards=board_states,
        )

    async def launch(self) -> dict[str, Any]:
        async with self._lock:
            if await self.session.load_existing_debug_port():
                target = await self.session.find_target("waterlooworks.uwaterloo.ca")
                await self.session.activate_or_create_target(target)
                self.snapshot.browser_open = True
                self.snapshot.status = "waiting_for_login"
                self.snapshot.message = (
                    "Chrome is open. Complete Waterloo SSO/MFA; the importer will open "
                    "each board and click All Jobs automatically."
                )
                return self.snapshot.payload()
            try:
                await self.session.launch()
            except RuntimeError:
                self.snapshot.status = "failed"
                self.snapshot.message = (
                    "Chrome opened, but its local connector did not become ready."
                )
                raise
            self._minimize_attempted_for_login = False
            self.snapshot.browser_open = True
            self.snapshot.status = "waiting_for_login"
            self.snapshot.message = (
                "Complete Waterloo SSO/MFA; the importer will open each board and click "
                "All Jobs automatically."
            )
            return self.snapshot.payload()

    async def get_status(self) -> dict[str, Any]:
        if self.snapshot.status in {"collecting", "importing"}:
            return self.snapshot.payload()
        async with self._lock:
            connected = await self.session.load_existing_debug_port()
            if not connected:
                if self.snapshot.browser_open:
                    self.snapshot.status = "idle"
                    self.snapshot.message = "The dedicated Chrome window is closed."
                self.snapshot.browser_open = False
                self.snapshot.page_url = None
                return self.snapshot.payload()

            self.snapshot.browser_open = True
            if self.snapshot.status in {"completed", "partial", "failed"}:
                return self.snapshot.payload()
            target = await self.session.find_target("waterlooworks.uwaterloo.ca")
            if not target:
                self.snapshot.status = "waiting_for_login"
                self.snapshot.message = (
                    "Waiting for Waterloo SSO/MFA to return to WaterlooWorks…"
                )
                self.snapshot.page_url = None
                return self.snapshot.payload()

            self.snapshot.page_url = target.get("url")
            try:
                readiness = await self.session.evaluate(
                    target,
                    "({authenticated: location.pathname.startsWith('/myAccount/'), "
                    "hasPostingApi: typeof getPostingData === 'function' && "
                    "typeof getPostingOverview === 'function', "
                    "hasJobTable: Boolean(document.querySelector('table tbody')), "
                    "title: document.title})",
                    timeout=8,
                )
            except Exception:
                readiness = {}
            if readiness.get("authenticated"):
                self.snapshot.status = "ready"
                minimized_now = False
                if not self._minimize_attempted_for_login:
                    self._minimize_attempted_for_login = True
                    try:
                        minimized_now = await self.session.minimize_window(target)
                    except Exception:
                        minimized_now = False
                if readiness.get("hasPostingApi") and readiness.get("hasJobTable"):
                    self.snapshot.message = (
                        "WaterlooWorks is connected and ready to import."
                    )
                else:
                    self.snapshot.message = (
                        "WaterlooWorks is connected. Import will open All Jobs on each board, "
                        "mark inaccessible boards as failed, and continue."
                    )
                if minimized_now:
                    self.snapshot.message += " The login window was minimized automatically."
            else:
                self._minimize_attempted_for_login = False
                self.snapshot.status = "waiting_for_login"
                self.snapshot.message = (
                    "Complete Waterloo SSO/MFA in the dedicated Chrome window."
                )
            return self.snapshot.payload()

    async def start_collection(self) -> dict[str, Any]:
        async with self._lock:
            if self.collect_task and not self.collect_task.done():
                return self.snapshot.payload()
            target = await self.session.find_target("waterlooworks.uwaterloo.ca")
            if not target:
                raise RuntimeError("WaterlooWorks is not connected yet.")
            readiness = await self.session.evaluate(
                target,
                "({authenticated: location.pathname.startsWith('/myAccount/')})",
                timeout=8,
            )
            if not readiness.get("authenticated"):
                raise RuntimeError("Complete Waterloo SSO/MFA before importing.")
            self.snapshot = WaterlooWorksSnapshot(
                status="collecting",
                message="Reading WaterlooWorks job pages…",
                browser_open=True,
                page_url=target.get("url"),
                started_at=datetime.now(UTC).isoformat(),
            )
            collector = WaterlooWorksCollector(
                session=self.session,
                repository=self.repository,
                snapshot=self.snapshot,
            )
            self.collect_task = asyncio.create_task(collector.collect_all(target))
            return self.snapshot.payload()

    async def list_jobs(
        self,
        *,
        board: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
        include_description: bool = False,
    ) -> dict[str, Any]:
        allowed_boards = {name for name, _ in WATERLOOWORKS_BOARDS}
        if board and board not in allowed_boards:
            raise RuntimeError(f"Unknown WaterlooWorks board: {board}")
        return await asyncio.to_thread(
            self.repository.list_jobs,
            board=board,
            query=query,
            limit=limit,
            offset=offset,
            include_description=include_description,
        )

    async def get_job(self, source_job_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self.repository.get_job, source_job_id)

    async def close(self) -> None:
        if self.collect_task and not self.collect_task.done():
            self.collect_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.collect_task
        await self.session.close()
        self._minimize_attempted_for_login = False
