"""Navigate WaterlooWorks boards and read their authenticated APIs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from wecanfindintern.waterlooworks.application.ports import WaterlooWorksBoardUnavailable
from wecanfindintern.waterlooworks.infrastructure.browser.job_extraction import (
    build_extract_jobs_script,
)
from wecanfindintern.waterlooworks.infrastructure.browser.scripts import (
    WATERLOOWORKS_API_READINESS_SCRIPT,
)
from wecanfindintern.waterlooworks.infrastructure.browser.session import ChromeSession

BOARD_SHELL_ATTEMPTS = 40
BOARD_SHELL_POLL_SECONDS = 0.25
ALL_JOBS_ATTEMPTS = 12
ALL_JOBS_POLL_SECONDS = 0.25
RESULT_ACTIVATION_ATTEMPTS = 20
RESULT_ACTIVATION_POLL_SECONDS = 0.15
API_READINESS_ATTEMPTS = 24
API_READINESS_POLL_SECONDS = 0.25


class ChromeJobReader:
    def __init__(self, session: ChromeSession) -> None:
        self.session = session

    async def read_board(
        self,
        target: dict[str, Any],
        *,
        board_name: str,
        board_url: str,
        known_job_ids: set[str],
        on_display_mode: Callable[[str], None],
    ) -> list[dict[str, Any]]:
        await self.session.navigate(target, board_url)
        await self._wait_for_board_shell(target, board_name, board_url)
        display_mode = await self._click_all_jobs(target, board_name)
        on_display_mode(display_mode)
        await self._wait_for_board_ready(target, board_name, board_url)
        result = await self.session.evaluate(
            target, build_extract_jobs_script(known_job_ids), timeout=1800
        )
        raw_jobs = result.get("jobs") if isinstance(result, dict) else None
        if not isinstance(raw_jobs, list):
            raise RuntimeError("invalid collection result")
        return raw_jobs

    async def _wait_for_board_shell(
        self,
        target: dict[str, Any],
        board_name: str,
        board_url: str,
    ) -> None:
        expected_path = urlsplit(board_url).path
        last_path = ""
        for _ in range(BOARD_SHELL_ATTEMPTS):
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
            await asyncio.sleep(BOARD_SHELL_POLL_SECONDS)
        if last_path and last_path != expected_path:
            raise RuntimeError(f"redirected to {last_path}; expected {expected_path}")
        raise RuntimeError(f"{board_name} page did not finish loading")

    async def _click_all_jobs(
        self,
        target: dict[str, Any],
        board_name: str,
    ) -> str:
        """Initialize the board's complete result set before API extraction."""

        for _ in range(ALL_JOBS_ATTEMPTS):
            result = await self.session.evaluate(
                target,
                r"""
                (() => {
                  const normalize = (value) => String(value || "")
                    .replace(/\s+/g, " ")
                    .trim()
                    .toLowerCase();
                  const visible = (element) => {
                    if (!element) return false;
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.display !== "none" && style.visibility !== "hidden" &&
                      rect.width > 0 && rect.height > 0;
                  };
                  const label = (element) => normalize(
                    element.innerText || element.textContent || element.value ||
                    element.getAttribute("aria-label")
                  );
                  const modeForToggle = (toggle) => {
                    const toggleLabel = normalize(toggle?.getAttribute("aria-label"));
                    if (toggleLabel === "table mode") return "card";
                    if (toggleLabel === "card mode") return "table";
                    const table = document.querySelector("table.data-viewer-table");
                    if (visible(table)) return "table";
                    const card = [...document.querySelectorAll('[id^="resultRow_"]')]
                      .find(visible);
                    return card ? "card" : "unknown";
                  };
                  const pageText = normalize(document.body.innerText);
                  const unavailable = pageText.includes(
                    "to search for jobs, ensure the following"
                  ) && [
                    "you are scheduled out to work for the upcoming term",
                    "you have accepted the terms & conditions",
                    "you are not restricted from applying to jobs",
                  ].some((marker) => pageText.includes(marker));
                  if (unavailable) {
                    return {
                      unavailable: true,
                      reason: "WaterlooWorks does not offer job search on this board " +
                        "for the current account or recruiting term",
                    };
                  }
                  const modeToggle = [...document.querySelectorAll(
                    'button[aria-label="Card Mode"], button[aria-label="Table Mode"]'
                  )].find(visible);
                  const showSearch = [...document.querySelectorAll(".js--show-search")]
                    .find(visible);
                  if (showSearch && modeToggle) {
                    return {activated: true, mode: modeForToggle(modeToggle)};
                  }
                  const candidates = [...document.querySelectorAll(
                    ".tag-rail button, .tag-rail [role='button'], " +
                    "button.btn__default.pill"
                  )];
                  const allJobs = candidates.find((element) =>
                    label(element) === "all jobs" && visible(element)
                  );
                  if (!allJobs) {
                    return {
                      clicked: false,
                      reason: "no visible control labelled All Jobs",
                    };
                  }
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
            if result.get("unavailable"):
                raise WaterlooWorksBoardUnavailable(f"{board_name}: {result['reason']}")
            if result.get("activated"):
                return str(result.get("mode") or "unknown")
            if result.get("clicked"):
                for _ in range(RESULT_ACTIVATION_ATTEMPTS):
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
                          const showSearch = [...document.querySelectorAll(
                            ".js--show-search"
                          )].find(visible);
                          const modeToggle = [...document.querySelectorAll(
                            'button[aria-label="Card Mode"], ' +
                            'button[aria-label="Table Mode"]'
                          )].find(visible);
                          const toggleLabel = String(
                            modeToggle?.getAttribute("aria-label") || ""
                          ).trim().toLowerCase();
                          const mode = toggleLabel === "table mode"
                            ? "card"
                            : toggleLabel === "card mode" ? "table" : "unknown";
                          return {
                            activated: visible(showSearch) && visible(modeToggle),
                            mode,
                          };
                        })()
                        """,
                        timeout=5,
                    )
                    if activation.get("activated"):
                        return str(activation.get("mode") or "unknown")
                    await asyncio.sleep(RESULT_ACTIVATION_POLL_SECONDS)
                raise RuntimeError(f"{board_name} All Jobs click did not activate the result set")
            await asyncio.sleep(ALL_JOBS_POLL_SECONDS)
        raise RuntimeError(f"{board_name} did not expose an All Jobs button")

    async def _wait_for_board_ready(
        self,
        target: dict[str, Any],
        board_name: str,
        board_url: str,
    ) -> None:
        expected_path = urlsplit(board_url).path
        last_path = ""
        for _ in range(API_READINESS_ATTEMPTS):
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
            await asyncio.sleep(API_READINESS_POLL_SECONDS)
        if last_path and last_path != expected_path:
            raise RuntimeError(f"redirected to {last_path}; expected {expected_path}")
        raise RuntimeError(f"{board_name} did not expose the authenticated job API")
