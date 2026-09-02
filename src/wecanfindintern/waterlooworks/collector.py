"""Board-by-board WaterlooWorks collection state machine."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from wecanfindintern.waterlooworks.browser import ChromeSession
from wecanfindintern.waterlooworks.browser_scripts import (
    WATERLOOWORKS_API_READINESS_SCRIPT,
)
from wecanfindintern.waterlooworks.config import WATERLOOWORKS_BOARDS
from wecanfindintern.waterlooworks.extractor import (
    EXTRACT_JOBS_SCRIPT,
)
from wecanfindintern.waterlooworks.repository import WaterlooWorksRepository
from wecanfindintern.waterlooworks.state import WaterlooWorksSnapshot


class WaterlooWorksCollector:
    """Drive one full import across all boards, updating the shared snapshot."""

    def __init__(
        self,
        *,
        session: ChromeSession,
        repository: WaterlooWorksRepository,
        snapshot: WaterlooWorksSnapshot,
    ) -> None:
        self.session = session
        self.repository = repository
        self.snapshot = snapshot

    async def collect_all(self, target: dict[str, Any]) -> None:
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
                    posting_inserted_count=0,
                    posting_known_count=0,
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
                    await self.session.navigate(target, board_url)
                    await self._wait_for_board_shell(target, board_name, board_url)
                    self.snapshot.message = (
                        f"Opening All Jobs on board {index}/{len(WATERLOOWORKS_BOARDS)}: "
                        f"{board_name.replace('_', ' ').title()}…"
                    )
                    await self._click_all_jobs(target, board_name)
                    self.snapshot.message = (
                        f"Reading the job API for board {index}/{len(WATERLOOWORKS_BOARDS)}: "
                        f"{board_name.replace('_', ' ').title()}…"
                    )
                    await self._wait_for_board_ready(target, board_name, board_url)
                    result = await self.session.evaluate(
                        target, EXTRACT_JOBS_SCRIPT, timeout=1800
                    )
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
                        posting_inserted_count=outcomes["posting_inserted"],
                        posting_known_count=outcomes["posting_known"],
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
            self.snapshot.posting_inserted_count = totals["posting_inserted_count"]
            self.snapshot.posting_known_count = totals["posting_known_count"]
            self.snapshot.posting_failed_count = totals["posting_failed_count"]
            self.snapshot.board_failed_count = totals["board_failed_count"]
            self.snapshot.finished_at = datetime.now(UTC).isoformat()
            self.snapshot.last_job_update_at = await asyncio.to_thread(
                self.repository.latest_job_update_at
            )
            self.snapshot.status = totals["status"]
            self.snapshot.message = (
                f"{totals['posting_failed_count']} posting failures across "
                f"{totals['board_failed_count']} boards."
                if totals["posting_failed_count"] or totals["board_failed_count"]
                else ""
            )
        except asyncio.CancelledError:
            if run_id is not None:
                await asyncio.to_thread(
                    self.repository.fail_run, run_id, "Collection cancelled"
                )
            raise
        except Exception as error:
            if run_id is not None:
                await asyncio.to_thread(self.repository.fail_run, run_id, str(error))
            self.snapshot.status = "failed"
            self.snapshot.message = str(error)
            self.snapshot.finished_at = datetime.now(UTC).isoformat()

    def _board_state(self, board_name: str) -> dict[str, Any]:
        return next(board for board in self.snapshot.boards if board["name"] == board_name)

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
                readiness = await self.session.evaluate(
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
        """Initialize the board's complete result set before API extraction."""

        for _ in range(30):
            result = await self.session.evaluate(
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
                    const pageText = normalize(document.body.innerText);
                    return {
                      clicked: false,
                      tableReady: false,
                      unavailableReason: pageText.includes(
                        "to search for jobs, ensure the following"
                      )
                        ? "WaterlooWorks does not offer All Jobs on this board " +
                          "for the current account or recruiting term"
                        : null,
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
            if result.get("unavailableReason"):
                raise RuntimeError(f"{board_name}: {result['unavailableReason']}")
            if result.get("clicked"):
                for _ in range(30):
                    activation = await self.session.evaluate(
                        target,
                        r"""
                        (() => {
                          const visible = (element) => {
                            if (!element) return false;
                            const style = getComputedStyle(element);
                            const rect = element.getBoundingClientRect();
                            return style.display !== "none" &&
                              style.visibility !== "hidden" &&
                              rect.width > 0 && rect.height > 0;
                          };
                          const showSearch = document.querySelector(".js--show-search");
                          const modeToggle = document.querySelector(
                            'button[aria-label="Card Mode"], ' +
                            'button[aria-label="Table Mode"]'
                          );
                          return {
                            activated: visible(showSearch) && visible(modeToggle),
                          };
                        })()
                        """,
                        timeout=5,
                    )
                    if activation.get("activated"):
                        return
                    await asyncio.sleep(0.25)
                raise RuntimeError(
                    f"{board_name} All Jobs click did not activate the result set"
                )
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
                readiness = await self.session.evaluate(
                    target,
                    WATERLOOWORKS_API_READINESS_SCRIPT,
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
        raise RuntimeError(f"{board_name} did not expose the authenticated job API")
