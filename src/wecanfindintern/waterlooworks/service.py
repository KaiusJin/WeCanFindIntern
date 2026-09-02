"""Orchestrate the WaterlooWorks browser session and collection lifecycle."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from wecanfindintern.waterlooworks.applications import (
    EXTRACT_APPLICATIONS_SCRIPT,
    OPEN_TOTAL_SUBMITTED_SCRIPT,
    WATERLOOWORKS_APPLICATIONS_URL,
)
from wecanfindintern.waterlooworks.browser import ChromeSession, find_chrome_binary
from wecanfindintern.waterlooworks.browser_scripts import (
    WATERLOOWORKS_API_READINESS_SCRIPT,
)
from wecanfindintern.waterlooworks.collector import WaterlooWorksCollector
from wecanfindintern.waterlooworks.config import (
    WATERLOOWORKS_BOARDS,
    waterlooworks_database_path,
    waterlooworks_profile_path,
)
from wecanfindintern.waterlooworks.repository import WaterlooWorksRepository
from wecanfindintern.waterlooworks.state import (
    WaterlooWorksSnapshot,
    initial_board_states,
)

ApplicationSync = Callable[[dict[str, Any]], Awaitable[object]]


class WaterlooWorksService:
    """Own the Chrome session, the status snapshot, and one collection task."""

    def __init__(self) -> None:
        self.profile_dir = waterlooworks_profile_path()
        self.start_url = os.getenv("WATERLOOWORKS_URL", WATERLOOWORKS_BOARDS[0][1])
        self.repository = WaterlooWorksRepository(waterlooworks_database_path())
        self.session = ChromeSession(
            profile_dir=self.profile_dir,
            start_url=self.start_url,
            chrome_binary=os.getenv("WATERLOOWORKS_CHROME_BINARY") or find_chrome_binary(),
        )
        self.snapshot = self._snapshot_from_latest_run()
        self.snapshot.unique_job_count = self.repository.count_jobs()
        self.snapshot.application_count = self.repository.count_applications()
        self.snapshot.last_tracker_update_at = self.repository.latest_application_sync_at()
        self.snapshot.last_job_update_at = self.repository.latest_job_update_at()
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
        posting_known_count = sum(
            int(board["posting_known_count"]) for board in board_states
        )
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

    async def launch(self) -> dict[str, Any]:
        async with self._lock:
            if await self.session.load_existing_debug_port():
                target = await self.session.find_target("waterlooworks.uwaterloo.ca")
                await self.session.activate_or_create_target(target)
                self.snapshot.browser_open = True
                self.snapshot.status = "waiting_for_login"
                self.snapshot.message = (
                    "Chrome is open. Complete Waterloo SSO/MFA; the importer will open "
                    "All Jobs on each board, then read its authenticated job API."
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
                "Complete Waterloo SSO/MFA; the importer will open All Jobs on each board, "
                "then read its authenticated job API."
            )
            return self.snapshot.payload()

    async def get_status(self) -> dict[str, Any]:
        if self.snapshot.status in {"collecting", "importing", "syncing_applications"}:
            return self.snapshot.payload()
        async with self._lock:
            connected = await self.session.load_existing_debug_port()
            if not connected:
                if self.snapshot.browser_open:
                    self.snapshot.status = "idle"
                    self.snapshot.message = "The dedicated Chrome window is closed."
                self.snapshot.browser_open = False
                self.snapshot.page_url = None
                self._minimize_attempted_for_login = False
                return self.snapshot.payload()

            target = await self.session.find_target("waterlooworks.uwaterloo.ca")
            if not target:
                if self.snapshot.status in {"completed", "partial", "failed", "ready"}:
                    self.snapshot.status = "idle"
                    self.snapshot.message = "The dedicated WaterlooWorks window is closed."
                    self.snapshot.browser_open = False
                    self.snapshot.page_url = None
                    self._minimize_attempted_for_login = False
                    return self.snapshot.payload()
                self.snapshot.browser_open = True
                self.snapshot.status = "waiting_for_login"
                self.snapshot.message = (
                    "Waiting for Waterloo SSO/MFA to return to WaterlooWorks…"
                )
                self.snapshot.page_url = None
                return self.snapshot.payload()

            try:
                window_state = await self.session.target_window_state(target)
            except Exception:
                window_state = None
            if window_state == "minimized":
                self.snapshot.status = "idle"
                self.snapshot.message = (
                    "The dedicated WaterlooWorks window is closed or minimized."
                )
                self.snapshot.browser_open = False
                self.snapshot.page_url = None
                self._minimize_attempted_for_login = False
                return self.snapshot.payload()

            self.snapshot.browser_open = True
            if self.snapshot.status in {"completed", "partial", "failed"}:
                return self.snapshot.payload()
            self.snapshot.page_url = target.get("url")
            try:
                readiness = await self.session.evaluate(
                    target,
                    WATERLOOWORKS_API_READINESS_SCRIPT,
                    timeout=8,
                )
            except Exception:
                readiness = {}
            if readiness.get("authenticated"):
                self.snapshot.status = "ready"
                if readiness.get("ready"):
                    self.snapshot.message = (
                        "WaterlooWorks is connected and ready to import."
                    )
                else:
                    self.snapshot.message = (
                        "WaterlooWorks is connected. Import will open All Jobs and discover "
                        "each board's authenticated API, then continue past inaccessible boards."
                    )
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
            last_tracker_update_at = self.snapshot.last_tracker_update_at
            last_job_update_at = self.snapshot.last_job_update_at
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
                last_tracker_update_at=last_tracker_update_at,
                last_job_update_at=last_job_update_at,
            )
            collector = WaterlooWorksCollector(
                session=self.session,
                repository=self.repository,
                snapshot=self.snapshot,
            )
            self.collect_task = asyncio.create_task(collector.collect_all(target))
            return self.snapshot.payload()

    async def start_application_sync(
        self, sync_application: ApplicationSync
    ) -> dict[str, Any]:
        """Start an authenticated submitted-application import in the background."""

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
                raise RuntimeError("Complete Waterloo SSO/MFA before syncing applications.")
            last_job_update_at = self.snapshot.last_job_update_at
            self.snapshot.status = "syncing_applications"
            self.snapshot.message = "Opening Total Submitted applications…"
            self.snapshot.browser_open = True
            self.snapshot.started_at = datetime.now(UTC).isoformat()
            self.snapshot.finished_at = None
            self.snapshot.last_job_update_at = last_job_update_at
            self.snapshot.application_new_job_count = 0
            self.snapshot.tracker_synced_count = 0
            self.snapshot.application_failed_count = 0
            self.collect_task = asyncio.create_task(
                self._sync_applications(target, sync_application)
            )
            return self.snapshot.payload()

    async def _sync_applications(
        self,
        target: dict[str, Any],
        sync_application: ApplicationSync,
    ) -> None:
        try:
            self.snapshot.page_url = WATERLOOWORKS_APPLICATIONS_URL
            target = await self._navigate_to_applications(target)
            await self._wait_for_applications_page(target)
            target = await self._open_total_submitted(target)
            self.snapshot.message = (
                "Reading submitted applications and complete job descriptions…"
            )
            result = await self.session.evaluate(
                target, EXTRACT_APPLICATIONS_SCRIPT, timeout=1800
            )
            raw_applications = (
                result.get("applications") if isinstance(result, dict) else None
            )
            if not isinstance(raw_applications, list):
                raise RuntimeError("Invalid WaterlooWorks application result")
            stored, counts, storage_errors = await asyncio.to_thread(
                self.repository.store_applications, raw_applications
            )
            tracker_errors: list[str] = []
            synced = 0
            for index, job in enumerate(stored, start=1):
                if index == 1 or index % 10 == 0:
                    self.snapshot.message = (
                        f"Adding submitted applications to Tracker: {index}/{len(stored)}…"
                    )
                try:
                    await sync_application(job)
                    synced += 1
                except Exception as error:
                    tracker_errors.append(f"{job.get('source_job_id')}: {error}")
            failures = counts["failed"] + len(tracker_errors)
            (
                self.snapshot.application_count,
                self.snapshot.unique_job_count,
                self.snapshot.last_job_update_at,
            ) = await asyncio.gather(
                asyncio.to_thread(self.repository.count_applications),
                asyncio.to_thread(self.repository.count_jobs),
                asyncio.to_thread(self.repository.latest_job_update_at),
            )
            self.snapshot.application_new_job_count = counts["new_jobs"]
            self.snapshot.tracker_synced_count = synced
            self.snapshot.application_failed_count = failures
            self.snapshot.finished_at = datetime.now(UTC).isoformat()
            if synced:
                self.snapshot.last_tracker_update_at = self.snapshot.finished_at
            self.snapshot.status = "partial" if failures else "completed"
            detail_note = (
                f" {counts['detail_failures']} descriptions were unavailable."
                if counts["detail_failures"]
                else ""
            )
            self.snapshot.message = (
                f"{failures} application sync failures.{detail_note}"
                if failures
                else ""
            )
            if storage_errors or tracker_errors:
                self.snapshot.message += " " + "; ".join(
                    [*storage_errors, *tracker_errors][:2]
                )[:400]
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.snapshot.status = "failed"
            self.snapshot.message = str(error).splitlines()[0][:500]
            self.snapshot.finished_at = datetime.now(UTC).isoformat()

    async def _navigate_to_applications(
        self, target: dict[str, Any]
    ) -> dict[str, Any]:
        """Navigate and reacquire the page target if Chrome replaces it."""

        if str(target.get("url", "")).startswith(WATERLOOWORKS_APPLICATIONS_URL):
            return target
        try:
            await self.session.navigate(target, WATERLOOWORKS_APPLICATIONS_URL)
        except RuntimeError as error:
            if "Inspected target navigated or closed" not in str(error):
                raise

        for _ in range(80):
            refreshed = await self.session.find_target(WATERLOOWORKS_APPLICATIONS_URL)
            if refreshed:
                return refreshed
            await asyncio.sleep(0.25)
        raise RuntimeError(
            "WaterlooWorks replaced the browser page while navigating and the new "
            "Applications page could not be reconnected."
        )

    async def _open_total_submitted(
        self, target: dict[str, Any]
    ) -> dict[str, Any]:
        """Open Total Submitted, then bind to the resulting page context."""

        try:
            await self.session.evaluate(
                target, OPEN_TOTAL_SUBMITTED_SCRIPT, timeout=8
            )
        except RuntimeError as error:
            if "Inspected target navigated or closed" not in str(error):
                raise

        readiness_script = (
            "(() => [...document.querySelectorAll('table')].some(table => {"
            "const text = String(table.querySelector('thead')?.innerText || '');"
            "return text.includes('Job ID') && text.includes('App Status') && "
            "text.includes('App Submitted On');}))()"
        )
        for _ in range(120):
            refreshed = await self.session.find_target(WATERLOOWORKS_APPLICATIONS_URL)
            if refreshed:
                try:
                    if await self.session.evaluate(
                        refreshed, readiness_script, timeout=5
                    ):
                        return refreshed
                except Exception:
                    pass
            await asyncio.sleep(0.25)
        raise RuntimeError(
            "Total Submitted opened, but the WaterlooWorks applications table "
            "did not become ready."
        )

    async def _wait_for_applications_page(self, target: dict[str, Any]) -> None:
        for _ in range(80):
            try:
                ready = await self.session.evaluate(
                    target,
                    "({path: location.pathname, ready: document.readyState !== 'loading' && "
                    "[...document.querySelectorAll('table tr')].some(row => "
                    "String(row.querySelector('th,td')?.textContent || '').trim() === "
                    "'Total Submitted:')})",
                    timeout=5,
                )
                if (
                    ready.get("ready")
                    and ready.get("path") == "/myAccount/co-op/full/applications.htm"
                ):
                    return
            except Exception:
                pass
            await asyncio.sleep(0.25)
        raise RuntimeError("WaterlooWorks applications page did not become ready.")

    async def list_jobs(
        self,
        *,
        board: str | None = None,
        boards: list[str] | None = None,
        query: str | None = None,
        location: str | None = None,
        company: str | None = None,
        skill: str | None = None,
        category: str | None = None,
        city: str | None = None,
        region: str | None = None,
        country: str | None = None,
        work_modes: list[str] | None = None,
        opportunity_types: list[str] | None = None,
        posted_after: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        include_description: bool = False,
    ) -> dict[str, Any]:
        allowed_boards = {name for name, _ in WATERLOOWORKS_BOARDS} | {"applications"}
        selected_boards = list(dict.fromkeys([*(boards or []), *([board] if board else [])]))
        unknown_boards = [value for value in selected_boards if value not in allowed_boards]
        if unknown_boards:
            raise RuntimeError(f"Unknown WaterlooWorks board: {unknown_boards[0]}")
        return await asyncio.to_thread(
            self.repository.list_jobs,
            boards=selected_boards,
            query=query,
            location=location,
            company=company,
            skill=skill,
            category=category,
            city=city,
            region=region,
            country=country,
            work_modes=work_modes,
            opportunity_types=opportunity_types,
            posted_after=posted_after,
            limit=limit,
            cursor=cursor,
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
