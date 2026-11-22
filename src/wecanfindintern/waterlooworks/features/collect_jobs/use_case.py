"""Start one authenticated background collection."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from wecanfindintern.waterlooworks.application.ports import JobReader
from wecanfindintern.waterlooworks.application.runtime import WaterlooWorksRuntime
from wecanfindintern.waterlooworks.application.state import WaterlooWorksSnapshot
from wecanfindintern.waterlooworks.features.collect_jobs.collector import WaterlooWorksCollector


class CollectJobs:
    def __init__(self, runtime: WaterlooWorksRuntime, reader: JobReader) -> None:
        self.runtime = runtime
        self.reader = reader

    async def execute(self) -> dict[str, Any]:
        async with self.runtime.lock:
            if self.runtime.active_task and not self.runtime.active_task.done():
                return self.runtime.snapshot.payload()
            last_tracker_update_at = self.runtime.snapshot.last_tracker_update_at
            last_job_update_at = self.runtime.snapshot.last_job_update_at
            target = await self.runtime.session.find_target("waterlooworks.uwaterloo.ca")
            if not target:
                raise RuntimeError("WaterlooWorks is not connected yet.")
            if not await self.runtime.session.is_authenticated(target):
                raise RuntimeError("Complete Waterloo SSO/MFA before importing.")
            self.runtime.snapshot = WaterlooWorksSnapshot(
                status="collecting",
                message="Reading WaterlooWorks job pages…",
                browser_open=True,
                page_url=target.get("url"),
                started_at=datetime.now(UTC).isoformat(),
                last_tracker_update_at=last_tracker_update_at,
                last_job_update_at=last_job_update_at,
            )
            collector = WaterlooWorksCollector(
                reader=self.reader,
                repository=self.runtime.repository,
                snapshot=self.runtime.snapshot,
            )
            self.runtime.active_task = asyncio.create_task(collector.collect_all(target))
            return self.runtime.snapshot.payload()
