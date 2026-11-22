"""Navigate Total Submitted and extract its authenticated application records."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from wecanfindintern.waterlooworks.domain.boards import WATERLOOWORKS_APPLICATIONS_URL
from wecanfindintern.waterlooworks.infrastructure.browser.application_extraction import (
    EXTRACT_APPLICATIONS_SCRIPT,
    OPEN_TOTAL_SUBMITTED_SCRIPT,
)
from wecanfindintern.waterlooworks.infrastructure.browser.session import ChromeSession


class ChromeApplicationReader:
    def __init__(self, session: ChromeSession) -> None:
        self.session = session

    async def read_applications(
        self,
        target: dict[str, Any],
        *,
        on_reading: Callable[[], None],
    ) -> list[dict[str, Any]]:
        target = await self._navigate_to_applications(target)
        await self._wait_for_applications_page(target)
        target = await self._open_total_submitted(target)
        on_reading()
        result = await self.session.evaluate(target, EXTRACT_APPLICATIONS_SCRIPT, timeout=1800)
        raw_applications = result.get("applications") if isinstance(result, dict) else None
        if not isinstance(raw_applications, list):
            raise RuntimeError("Invalid WaterlooWorks application result")
        return raw_applications

    async def _navigate_to_applications(self, target: dict[str, Any]) -> dict[str, Any]:
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

    async def _open_total_submitted(self, target: dict[str, Any]) -> dict[str, Any]:
        """Open Total Submitted, then bind to the resulting page context."""

        try:
            await self.session.evaluate(target, OPEN_TOTAL_SUBMITTED_SCRIPT, timeout=8)
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
                    if await self.session.evaluate(refreshed, readiness_script, timeout=5):
                        return refreshed
                except Exception:
                    pass
            await asyncio.sleep(0.25)
        raise RuntimeError(
            "Total Submitted opened, but the WaterlooWorks applications table did not become ready."
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
