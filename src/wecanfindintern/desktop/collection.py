"""Resident public-job collection scheduler used by the desktop sidecar."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.collection.run_collection_campaign import acquire_process_lock, run

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CollectionStatus:
    enabled: bool
    running: bool = False
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_error: str | None = None
    run_count: int = 0

    def payload(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": self.running,
            "last_started_at": (self.last_started_at.isoformat() if self.last_started_at else None),
            "last_finished_at": (
                self.last_finished_at.isoformat() if self.last_finished_at else None
            ),
            "last_error": self.last_error,
            "run_count": self.run_count,
        }


class BackgroundCollectionService:
    def __init__(self, *, config_path: Path, lock_path: Path) -> None:
        self.interval_seconds = max(
            300, int(os.getenv("WCFI_COLLECTION_INTERVAL_SECONDS", "14400"))
        )
        self.initial_delay_seconds = max(
            0, int(os.getenv("WCFI_COLLECTION_INITIAL_DELAY_SECONDS", "30"))
        )
        self.batch_size = int(os.getenv("WCFI_COLLECTION_BATCH_SIZE", "250"))
        self.concurrency = int(os.getenv("WCFI_COLLECTION_CONCURRENCY", "4"))
        self.max_retries = int(os.getenv("WCFI_COLLECTION_MAX_RETRIES", "3"))
        self.config_path = config_path
        self.lock_path = lock_path
        self.state_path = lock_path.with_name("collection-status.json")
        enabled = os.getenv("WCFI_BACKGROUND_COLLECTION_ENABLED", "0").lower()
        self.status = self._load_status(enabled=enabled in {"1", "true", "yes"})
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self.status.enabled and self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    def trigger(self) -> bool:
        if not self.status.enabled or self.status.running:
            return False
        self._wake.set()
        return True

    async def _loop(self) -> None:
        delay = self._next_delay_seconds()
        if delay:
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            self._wake.clear()
        while True:
            await self._run_once()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self.interval_seconds)
            self._wake.clear()

    async def _run_once(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock_file:
            try:
                acquire_process_lock(lock_file)
            except BlockingIOError:
                logger.info("Skipping scheduled collection because another run owns the lock")
                return
            self.status.running = True
            self.status.last_started_at = datetime.now(UTC)
            self.status.last_error = None
            self._persist_status()
            cancelled = False
            try:
                await run(
                    self.config_path,
                    self.batch_size,
                    concurrency=self.concurrency,
                    max_retries=self.max_retries,
                )
                self.status.run_count += 1
            except asyncio.CancelledError:
                cancelled = True
                self.status.last_error = "Collection was interrupted during app shutdown."
                self.status.last_finished_at = None
                raise
            except Exception as error:
                self.status.last_error = f"{type(error).__name__}: {error}"
                logger.exception("Scheduled collection failed")
            finally:
                self.status.running = False
                if not cancelled:
                    self.status.last_finished_at = datetime.now(UTC)
                self._persist_status()

    def _next_delay_seconds(self) -> float:
        if self.status.last_finished_at is None:
            return float(self.initial_delay_seconds)
        elapsed = (datetime.now(UTC) - self.status.last_finished_at).total_seconds()
        remaining = max(0.0, self.interval_seconds - elapsed)
        if remaining == 0:
            return float(self.initial_delay_seconds)
        return min(float(self.interval_seconds), remaining)

    def _load_status(self, *, enabled: bool) -> CollectionStatus:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            status = CollectionStatus(
                enabled=enabled,
                running=False,
                last_started_at=self._parse_datetime(payload.get("last_started_at")),
                last_finished_at=self._parse_datetime(payload.get("last_finished_at")),
                last_error=payload.get("last_error"),
                run_count=max(0, int(payload.get("run_count", 0))),
            )
            if payload.get("running"):
                status.last_error = "Previous collection was interrupted; it will retry."
                status.last_finished_at = None
            return status
        except FileNotFoundError:
            return CollectionStatus(enabled=enabled)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            logger.warning("Ignoring invalid collection status file: %s", error)
            return CollectionStatus(enabled=enabled)

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    def _persist_status(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.state_path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(self.status.payload(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if os.name != "nt":
            temporary_path.chmod(0o600)
        temporary_path.replace(self.state_path)
