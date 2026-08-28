"""Manage one local, dedicated Chrome session for WaterlooWorks collection."""

from __future__ import annotations

import asyncio
import json
import os
import platform
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import websockets

from wecanfindintern.waterlooworks.extractor import (
    EXTRACT_JOBS_SCRIPT,
    WATERLOOWORKS_BOARDS,
)
from wecanfindintern.waterlooworks.repository import WaterlooWorksRepository


@dataclass(slots=True)
class WaterlooWorksSnapshot:
    status: str = "idle"
    message: str = "Open a dedicated Chrome window to connect WaterlooWorks."
    browser_open: bool = False
    page_url: str | None = None
    unique_job_count: int = 0
    posting_success_count: int = 0
    posting_failed_count: int = 0
    board_failed_count: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    run_id: str | None = None
    boards: list[dict[str, Any]] = field(default_factory=lambda: _initial_board_states())

    def payload(self) -> dict[str, Any]:
        return asdict(self)


class WaterlooWorksService:
    """Own the browser connector and one in-process collection task."""

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
        self.chrome_binary = os.getenv("WATERLOOWORKS_CHROME_BINARY") or _find_chrome_binary()
        self.snapshot = self._snapshot_from_latest_run()
        self.process: asyncio.subprocess.Process | None = None
        self.collect_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._debug_port: int | None = None
        self._browser_websocket_url: str | None = None

    def _snapshot_from_latest_run(self) -> WaterlooWorksSnapshot:
        latest = self.repository.latest_run()
        if not latest:
            return WaterlooWorksSnapshot()
        board_states = _initial_board_states()
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
            message="Showing the latest WaterlooWorks sync stored on this device.",
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
            if await self._load_existing_debug_port():
                target = await self._find_waterlooworks_target()
                if self._browser_websocket_url:
                    if target:
                        await self._cdp_call(
                            self._browser_websocket_url,
                            "Target.activateTarget",
                            {"targetId": target["id"]},
                            timeout=5,
                        )
                    else:
                        await self._cdp_call(
                            self._browser_websocket_url,
                            "Target.createTarget",
                            {"url": self.start_url},
                            timeout=5,
                        )
                self.snapshot.browser_open = True
                self.snapshot.status = "waiting_for_login"
                self.snapshot.message = (
                    "Chrome is open. Complete Waterloo SSO/MFA; the importer will open "
                    "each board and click All Jobs automatically."
                )
                return self.snapshot.payload()
            if not self.chrome_binary:
                raise RuntimeError(
                    "Google Chrome was not found. Set WATERLOOWORKS_CHROME_BINARY "
                    "to its executable."
                )

            self.profile_dir.mkdir(parents=True, exist_ok=True)
            active_port_file = self.profile_dir / "DevToolsActivePort"
            active_port_file.unlink(missing_ok=True)
            self.snapshot.status = "browser_starting"
            self.snapshot.message = "Starting a dedicated Chrome window…"
            self.snapshot.browser_open = False
            self.snapshot.page_url = None
            self.process = await asyncio.create_subprocess_exec(
                self.chrome_binary,
                f"--user-data-dir={self.profile_dir}",
                "--remote-debugging-address=127.0.0.1",
                "--remote-debugging-port=0",
                "--remote-allow-origins=http://localhost",
                "--no-first-run",
                "--no-default-browser-check",
                "--new-window",
                self.start_url,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            if not await self._wait_for_debug_port():
                self.snapshot.status = "failed"
                self.snapshot.message = (
                    "Chrome opened, but its local connector did not become ready."
                )
                raise RuntimeError(self.snapshot.message)
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
            connected = await self._load_existing_debug_port()
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
            target = await self._find_waterlooworks_target()
            if not target:
                self.snapshot.status = "waiting_for_login"
                self.snapshot.message = "Waiting for Waterloo SSO/MFA to return to WaterlooWorks…"
                self.snapshot.page_url = None
                return self.snapshot.payload()

            self.snapshot.page_url = target.get("url")
            try:
                readiness = await self._evaluate(
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
                if readiness.get("hasPostingApi") and readiness.get("hasJobTable"):
                    self.snapshot.message = "WaterlooWorks is connected and ready to import."
                else:
                    self.snapshot.message = (
                        "WaterlooWorks is connected. Import will open All Jobs on each board, "
                        "mark inaccessible boards as failed, and continue."
                    )
            else:
                self.snapshot.status = "waiting_for_login"
                self.snapshot.message = (
                    "Complete Waterloo SSO/MFA in the dedicated Chrome window."
                )
            return self.snapshot.payload()

    async def start_collection(self) -> dict[str, Any]:
        async with self._lock:
            if self.collect_task and not self.collect_task.done():
                return self.snapshot.payload()
            target = await self._find_waterlooworks_target()
            if not target:
                raise RuntimeError("WaterlooWorks is not connected yet.")
            readiness = await self._evaluate(
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
            self.collect_task = asyncio.create_task(self._collect(target))
            return self.snapshot.payload()

    async def list_jobs(
        self,
        *,
        board: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
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
        )

    async def close(self) -> None:
        if self.collect_task and not self.collect_task.done():
            self.collect_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.collect_task
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=3)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        self.process = None
        self._debug_port = None
        self._browser_websocket_url = None

    async def _collect(self, target: dict[str, Any]) -> None:
        run_id: str | None = None
        try:
            run_id = await asyncio.to_thread(
                self.repository.start_run,
                self.snapshot.boards,
            )
            self.snapshot.run_id = run_id
            board_errors: list[str] = []
            for index, (board_name, board_url) in enumerate(WATERLOOWORKS_BOARDS, start=1):
                board_state = self._board_state(board_name)
                board_state.update(
                    status="collecting",
                    discovered_count=0,
                    posting_success_count=0,
                    posting_failed_count=0,
                    error=None,
                )
                await asyncio.to_thread(
                    self.repository.mark_board_collecting,
                    run_id,
                    board_name,
                )
                self.snapshot.page_url = board_url
                self.snapshot.message = (
                    f"Reading WaterlooWorks board {index}/{len(WATERLOOWORKS_BOARDS)}: "
                    f"{board_name.replace('_', ' ').title()}…"
                )
                try:
                    await self._navigate(target, board_url)
                    await self._wait_for_board_shell(target, board_name, board_url)
                    self.snapshot.message = (
                        f"Opening All Jobs on board {index}/{len(WATERLOOWORKS_BOARDS)}: "
                        f"{board_name.replace('_', ' ').title()}…"
                    )
                    await self._click_all_jobs(target, board_name)
                    await self._wait_for_board_ready(target, board_name, board_url)
                    result = await self._evaluate(target, EXTRACT_JOBS_SCRIPT, timeout=1800)
                    raw_board_jobs = result.get("jobs") if isinstance(result, dict) else None
                    if not isinstance(raw_board_jobs, list):
                        raise RuntimeError("invalid collection result")
                    for raw in raw_board_jobs:
                        if isinstance(raw, dict):
                            raw["jobBoard"] = board_name
                            raw["jobBoardUrl"] = board_url
                    outcomes, posting_errors = await asyncio.to_thread(
                        self.repository.store_board_postings,
                        run_id,
                        board_name,
                        raw_board_jobs,
                    )
                    board_state.update(
                        status="completed",
                        discovered_count=len(raw_board_jobs),
                        posting_success_count=outcomes["posting_success"],
                        posting_failed_count=outcomes["posting_failed"],
                        error="; ".join(posting_errors[:3])[:500] or None,
                    )
                except Exception as error:
                    error_text = str(error).splitlines()[0][:240]
                    board_state.update(status="failed", error=error_text)
                    await asyncio.to_thread(
                        self.repository.mark_board_failed,
                        run_id,
                        board_name,
                        error_text,
                    )
                    board_errors.append(f"{board_name}: {error_text}")
                    self.snapshot.board_failed_count = len(board_errors)

            totals = await asyncio.to_thread(
                self.repository.finish_run,
                run_id,
                "; ".join(board_errors)[:1000] or None,
            )
            self.snapshot.unique_job_count = totals["unique_job_count"]
            self.snapshot.posting_success_count = totals["posting_success_count"]
            self.snapshot.posting_failed_count = totals["posting_failed_count"]
            self.snapshot.board_failed_count = totals["board_failed_count"]
            self.snapshot.finished_at = datetime.now(UTC).isoformat()
            self.snapshot.status = totals["status"]
            self.snapshot.message = (
                f"WaterlooWorks sync finished: {totals['posting_success_count']} postings "
                f"succeeded, {totals['posting_failed_count']} postings failed; "
                f"{totals['board_failed_count']} boards failed."
            )
        except asyncio.CancelledError:
            if run_id is not None:
                await asyncio.to_thread(self.repository.fail_run, run_id, "Collection cancelled")
            raise
        except Exception as error:
            if run_id is not None:
                await asyncio.to_thread(self.repository.fail_run, run_id, str(error))
            self.snapshot.status = "failed"
            self.snapshot.message = str(error)
            self.snapshot.finished_at = datetime.now(UTC).isoformat()

    def _board_state(self, board_name: str) -> dict[str, Any]:
        return next(board for board in self.snapshot.boards if board["name"] == board_name)

    async def _find_waterlooworks_target(self) -> dict[str, Any] | None:
        if not await self._load_existing_debug_port():
            return None
        assert self._debug_port is not None
        try:
            async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                response = await client.get(f"http://127.0.0.1:{self._debug_port}/json/list")
                response.raise_for_status()
                targets = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        return next(
            (
                target
                for target in targets
                if target.get("type") == "page"
                and "waterlooworks.uwaterloo.ca" in str(target.get("url", ""))
                and target.get("webSocketDebuggerUrl")
            ),
            None,
        )

    async def _navigate(self, target: dict[str, Any], url: str) -> None:
        await self._cdp_call(
            target["webSocketDebuggerUrl"],
            "Page.navigate",
            {"url": url},
            timeout=10,
        )

    async def _wait_for_board_shell(
        self,
        target: dict[str, Any],
        board_name: str,
        board_url: str,
    ) -> None:
        expected_path = urlsplit(board_url).path
        last_path = ""
        for _ in range(60):
            try:
                readiness = await self._evaluate(
                    target,
                    "({path: location.pathname, ready: document.readyState !== 'loading'})",
                    timeout=5,
                )
                last_path = readiness.get("path") or last_path
                if readiness.get("ready") and last_path == expected_path:
                    return
            except Exception:
                pass
            await asyncio.sleep(0.5)
        if last_path and last_path != expected_path:
            raise RuntimeError(f"redirected to {last_path}; expected {expected_path}")
        raise RuntimeError(f"{board_name} page did not finish loading")

    async def _click_all_jobs(
        self,
        target: dict[str, Any],
        board_name: str,
    ) -> None:
        for _ in range(30):
            result = await self._evaluate(
                target,
                r"""
                (() => {
                  const normalize = (value) => String(value || "")
                    .replace(/\s+/g, " ")
                    .trim()
                    .toLowerCase();
                  const visible = (element) => {
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.display !== "none" && style.visibility !== "hidden" &&
                      rect.width > 0 && rect.height > 0;
                  };
                  const label = (element) => normalize(
                    element.innerText || element.textContent || element.value ||
                    element.getAttribute("aria-label")
                  );
                  const allJobs = document.querySelector(
                    '.tag-rail > button.btn__default.pill'
                  );
                  if (!allJobs || label(allJobs) !== "all jobs" || !visible(allJobs)) {
                    return {
                      clicked: false,
                      tableReady: false,
                      reason: "missing exact .tag-rail > button.btn__default.pill All Jobs",
                    };
                  }
                  const controlDescription = normalize([
                    allJobs.getAttribute("aria-label"),
                    allJobs.title,
                    allJobs.className,
                  ].join(" "));
                  if (/(?:card|table).*(?:mode|view)|(?:mode|view).*(?:card|table)/.test(
                    controlDescription
                  )) return {clicked: false, rejectedModeToggle: true};
                  allJobs.scrollIntoView({block: "center"});
                  allJobs.click();
                  return {
                    clicked: true,
                    label: label(allJobs),
                    tag: allJobs.tagName,
                    id: allJobs.id || null,
                    className: String(allJobs.className || ""),
                  };
                })()
                """,
                timeout=5,
            )
            if result.get("clicked"):
                return
            if result.get("tableReady"):
                return
            await asyncio.sleep(0.5)
        raise RuntimeError(f"{board_name} did not expose an All Jobs button")

    async def _wait_for_board_ready(
        self,
        target: dict[str, Any],
        board_name: str,
        board_url: str,
    ) -> None:
        expected_path = urlsplit(board_url).path
        last_path = ""
        for _ in range(90):
            try:
                readiness = await self._evaluate(
                    target,
                    "({path: location.pathname, ready: document.readyState !== 'loading' && "
                    "typeof getPostingData === 'function' && "
                    "typeof getPostingOverview === 'function' && "
                    "(() => { const table = document.querySelector('table.data-viewer-table'); "
                    "if (!table) return false; const style = getComputedStyle(table); "
                    "const rect = table.getBoundingClientRect(); "
                    "const cardLayout = Boolean(document.querySelector("
                    "\".tag-rail button[aria-label='Table Mode']\")); "
                    "return style.display !== 'none' && style.visibility !== 'hidden' && "
                    "rect.width > 0 && rect.height > 0 && !cardLayout; })()})",
                    timeout=5,
                )
                last_path = readiness.get("path") or last_path
                if readiness.get("ready") and last_path == expected_path:
                    return
            except Exception:
                pass
            await asyncio.sleep(0.5)
        if last_path and last_path != expected_path:
            raise RuntimeError(f"redirected to {last_path}; expected {expected_path}")
        raise RuntimeError(f"{board_name} did not expose a job results table after All Jobs")

    async def _evaluate(
        self,
        target: dict[str, Any],
        expression: str,
        *,
        timeout: float,
    ) -> Any:
        response = await self._cdp_call(
            target["webSocketDebuggerUrl"],
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
                "userGesture": True,
            },
            timeout=timeout,
        )
        if response.get("exceptionDetails"):
            details = response["exceptionDetails"]
            description = (
                details.get("exception", {}).get("description")
                or details.get("text")
                or "WaterlooWorks page script failed."
            )
            raise RuntimeError(description)
        remote = response.get("result", {})
        if remote.get("subtype") == "error":
            raise RuntimeError(remote.get("description") or "WaterlooWorks page script failed.")
        return remote.get("value")

    async def _cdp_call(
        self,
        websocket_url: str,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        async def exchange() -> dict[str, Any]:
            async with websockets.connect(
                websocket_url,
                origin="http://localhost",
                max_size=None,
                open_timeout=5,
                close_timeout=2,
            ) as socket:
                await socket.send(json.dumps({"id": 1, "method": method, "params": params}))
                while True:
                    message = json.loads(await socket.recv())
                    if message.get("id") != 1:
                        continue
                    if "error" in message:
                        raise RuntimeError(
                            message["error"].get("message", "Chrome connector failed.")
                        )
                    return message.get("result", {})

        return await asyncio.wait_for(exchange(), timeout=timeout)

    async def _load_existing_debug_port(self) -> bool:
        active_port_file = self.profile_dir / "DevToolsActivePort"
        try:
            lines = active_port_file.read_text(encoding="utf-8").splitlines()
            port = int(lines[0])
        except (FileNotFoundError, IndexError, OSError, ValueError):
            self._debug_port = None
            self._browser_websocket_url = None
            return False
        try:
            async with httpx.AsyncClient(timeout=1.5, trust_env=False) as client:
                response = await client.get(f"http://127.0.0.1:{port}/json/version")
                response.raise_for_status()
                version = response.json()
        except (httpx.HTTPError, ValueError):
            self._debug_port = None
            self._browser_websocket_url = None
            return False
        self._debug_port = port
        self._browser_websocket_url = version.get("webSocketDebuggerUrl")
        return True

    async def _wait_for_debug_port(self) -> bool:
        for _ in range(60):
            if await self._load_existing_debug_port():
                return True
            if self.process and self.process.returncode is not None:
                return False
            await asyncio.sleep(0.25)
        return False


def _find_chrome_binary() -> str | None:
    candidates: list[Path]
    if platform.system() == "Darwin":
        candidates = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    elif platform.system() == "Windows":
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        ]
    else:
        candidates = [Path("/usr/bin/google-chrome"), Path("/usr/bin/chromium")]
    return next((str(path) for path in candidates if path.is_file()), None)


def _initial_board_states() -> list[dict[str, Any]]:
    labels = {
        "full_cycle": "Co-op: Full-Cycle",
        "employer_student_direct": "Employer-Student Direct",
        "graduating": "Graduating jobs",
        "contract": "Contract jobs",
        "campus": "Campus jobs",
    }
    return [
        {
            "name": name,
            "label": labels[name],
            "url": url,
            "status": "pending",
            "discovered_count": 0,
            "posting_success_count": 0,
            "posting_failed_count": 0,
            "error": None,
        }
        for name, url in WATERLOOWORKS_BOARDS
    ]
