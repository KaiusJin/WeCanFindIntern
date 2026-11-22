"""Connect WaterlooWorks and report whether the local session is usable."""

from __future__ import annotations

from typing import Any

from wecanfindintern.waterlooworks.application.runtime import WaterlooWorksRuntime


class ConnectWaterlooWorks:
    def __init__(self, runtime: WaterlooWorksRuntime) -> None:
        self.runtime = runtime

    async def launch(self) -> dict[str, Any]:
        async with self.runtime.lock:
            if await self.runtime.session.load_existing_debug_port():
                target = await self.runtime.session.find_target("waterlooworks.uwaterloo.ca")
                await self.runtime.session.activate_or_create_target(target)
                self.runtime.snapshot.browser_open = True
                self.runtime.snapshot.status = "waiting_for_login"
                self.runtime.snapshot.message = (
                    "Chrome is open. Complete Waterloo SSO/MFA; the importer will open "
                    "All Jobs on each board, then read its authenticated job API."
                )
                return self.runtime.snapshot.payload()
            try:
                await self.runtime.session.launch()
            except RuntimeError:
                self.runtime.snapshot.status = "failed"
                self.runtime.snapshot.message = (
                    "Chrome opened, but its local connector did not become ready."
                )
                raise
            self.runtime.minimize_attempted_for_login = False
            self.runtime.snapshot.browser_open = True
            self.runtime.snapshot.status = "waiting_for_login"
            self.runtime.snapshot.message = (
                "Complete Waterloo SSO/MFA; the importer will open All Jobs on each board, "
                "then read its authenticated job API."
            )
            return self.runtime.snapshot.payload()

    async def get_status(self) -> dict[str, Any]:
        if self.runtime.snapshot.status in {"collecting", "importing", "syncing_applications"}:
            return self.runtime.snapshot.payload()
        async with self.runtime.lock:
            connected = await self.runtime.session.load_existing_debug_port()
            if not connected:
                if self.runtime.snapshot.browser_open:
                    self.runtime.snapshot.status = "idle"
                    self.runtime.snapshot.message = "The dedicated Chrome window is closed."
                self.runtime.snapshot.browser_open = False
                self.runtime.snapshot.page_url = None
                self.runtime.minimize_attempted_for_login = False
                return self.runtime.snapshot.payload()

            target = await self.runtime.session.find_target("waterlooworks.uwaterloo.ca")
            if not target:
                if self.runtime.snapshot.status in {"completed", "partial", "failed", "ready"}:
                    self.runtime.snapshot.status = "idle"
                    self.runtime.snapshot.message = "The dedicated WaterlooWorks window is closed."
                    self.runtime.snapshot.browser_open = False
                    self.runtime.snapshot.page_url = None
                    self.runtime.minimize_attempted_for_login = False
                    return self.runtime.snapshot.payload()
                self.runtime.snapshot.browser_open = True
                self.runtime.snapshot.status = "waiting_for_login"
                self.runtime.snapshot.message = (
                    "Waiting for Waterloo SSO/MFA to return to WaterlooWorks…"
                )
                self.runtime.snapshot.page_url = None
                return self.runtime.snapshot.payload()

            try:
                window_state = await self.runtime.session.target_window_state(target)
            except Exception:
                window_state = None
            if window_state == "minimized":
                self.runtime.snapshot.status = "idle"
                self.runtime.snapshot.message = (
                    "The dedicated WaterlooWorks window is closed or minimized."
                )
                self.runtime.snapshot.browser_open = False
                self.runtime.snapshot.page_url = None
                self.runtime.minimize_attempted_for_login = False
                return self.runtime.snapshot.payload()

            self.runtime.snapshot.browser_open = True
            if self.runtime.snapshot.status in {"completed", "partial", "failed"}:
                return self.runtime.snapshot.payload()
            self.runtime.snapshot.page_url = target.get("url")
            try:
                readiness = await self.runtime.session.readiness(target)
            except Exception:
                readiness = {}
            if readiness.get("authenticated"):
                self.runtime.snapshot.status = "ready"
                if readiness.get("ready"):
                    self.runtime.snapshot.message = (
                        "WaterlooWorks is connected and ready to import."
                    )
                else:
                    self.runtime.snapshot.message = (
                        "WaterlooWorks is connected. Import will open All Jobs and discover "
                        "each board's authenticated API, then continue past inaccessible boards."
                    )
            else:
                self.runtime.minimize_attempted_for_login = False
                self.runtime.snapshot.status = "waiting_for_login"
                self.runtime.snapshot.message = (
                    "Complete Waterloo SSO/MFA in the dedicated Chrome window."
                )
            return self.runtime.snapshot.payload()
