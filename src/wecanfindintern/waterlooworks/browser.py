"""CDP-based control of one dedicated Chrome session for WaterlooWorks."""

from __future__ import annotations

import asyncio
import json
import os
import platform
from pathlib import Path
from typing import Any

import httpx
import websockets


class ChromeSession:
    """Own a Chrome process and the CDP transport used to drive it."""

    def __init__(
        self,
        *,
        profile_dir: Path,
        start_url: str,
        chrome_binary: str | None,
    ) -> None:
        self.profile_dir = profile_dir
        self.start_url = start_url
        self.chrome_binary = chrome_binary
        self.process: asyncio.subprocess.Process | None = None
        self.debug_port: int | None = None
        self.websocket_url: str | None = None

    async def launch(self) -> None:
        """Start a dedicated Chrome window and wait for its debug endpoint."""

        if not self.chrome_binary:
            raise RuntimeError(
                "Google Chrome was not found. Set WATERLOOWORKS_CHROME_BINARY "
                "to its executable."
            )
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        active_port_file = self.profile_dir / "DevToolsActivePort"
        active_port_file.unlink(missing_ok=True)
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
        if not await self.wait_for_debug_port():
            raise RuntimeError(
                "Chrome opened, but its local connector did not become ready."
            )

    async def close(self) -> None:
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=3)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        self.process = None
        self.debug_port = None
        self.websocket_url = None

    async def load_existing_debug_port(self) -> bool:
        active_port_file = self.profile_dir / "DevToolsActivePort"
        try:
            lines = active_port_file.read_text(encoding="utf-8").splitlines()
            port = int(lines[0])
        except (FileNotFoundError, IndexError, OSError, ValueError):
            self.debug_port = None
            self.websocket_url = None
            return False
        try:
            async with httpx.AsyncClient(timeout=1.5, trust_env=False) as client:
                response = await client.get(f"http://127.0.0.1:{port}/json/version")
                response.raise_for_status()
                version = response.json()
        except (httpx.HTTPError, ValueError):
            self.debug_port = None
            self.websocket_url = None
            return False
        self.debug_port = port
        self.websocket_url = version.get("webSocketDebuggerUrl")
        return True

    async def wait_for_debug_port(self) -> bool:
        for _ in range(60):
            if await self.load_existing_debug_port():
                return True
            if self.process and self.process.returncode is not None:
                return False
            await asyncio.sleep(0.25)
        return False

    async def find_target(self, url_contains: str) -> dict[str, Any] | None:
        if not await self.load_existing_debug_port():
            return None
        assert self.debug_port is not None
        try:
            async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                response = await client.get(f"http://127.0.0.1:{self.debug_port}/json/list")
                response.raise_for_status()
                targets = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        return next(
            (
                target
                for target in targets
                if target.get("type") == "page"
                and url_contains in str(target.get("url", ""))
                and target.get("webSocketDebuggerUrl")
            ),
            None,
        )

    async def activate_or_create_target(self, target: dict[str, Any] | None) -> None:
        """Bring the WaterlooWorks page forward, creating it when missing."""

        if not self.websocket_url:
            return
        if target:
            await self.cdp_call(
                self.websocket_url,
                "Target.activateTarget",
                {"targetId": target["id"]},
                timeout=5,
            )
        else:
            await self.cdp_call(
                self.websocket_url,
                "Target.createTarget",
                {"url": self.start_url},
                timeout=5,
            )

    async def minimize_window(self, target: dict[str, Any]) -> bool:
        """Minimize the Chrome window containing the given WaterlooWorks page."""

        if not self.websocket_url or not target.get("id"):
            return False
        result = await self.cdp_call(
            self.websocket_url,
            "Browser.getWindowForTarget",
            {"targetId": target["id"]},
            timeout=5,
        )
        window_id = result.get("windowId")
        if not isinstance(window_id, int):
            return False
        await self.cdp_call(
            self.websocket_url,
            "Browser.setWindowBounds",
            {"windowId": window_id, "bounds": {"windowState": "minimized"}},
            timeout=5,
        )
        return True

    async def navigate(self, target: dict[str, Any], url: str) -> None:
        await self.cdp_call(
            target["webSocketDebuggerUrl"],
            "Page.navigate",
            {"url": url},
            timeout=10,
        )

    async def evaluate(
        self,
        target: dict[str, Any],
        expression: str,
        *,
        timeout: float,
    ) -> Any:
        response = await self.cdp_call(
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

    async def cdp_call(
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


def find_chrome_binary() -> str | None:
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
