"""Import submitted applications and synchronize their Tracker state."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from wecanfindintern.waterlooworks.application.ports import ApplicationReader, ApplicationSync
from wecanfindintern.waterlooworks.application.runtime import WaterlooWorksRuntime
from wecanfindintern.waterlooworks.domain.boards import WATERLOOWORKS_APPLICATIONS_URL


class SyncApplications:
    def __init__(self, runtime: WaterlooWorksRuntime, reader: ApplicationReader) -> None:
        self.runtime = runtime
        self.reader = reader

    def _report_reading(self) -> None:
        self.runtime.snapshot.message = (
            "Reading submitted applications and complete job descriptions…"
        )

    async def execute(self, sync_application: ApplicationSync) -> dict[str, Any]:
        """Start an authenticated submitted-application import in the background."""

        async with self.runtime.lock:
            if self.runtime.active_task and not self.runtime.active_task.done():
                return self.runtime.snapshot.payload()
            target = await self.runtime.session.find_target("waterlooworks.uwaterloo.ca")
            if not target:
                raise RuntimeError("WaterlooWorks is not connected yet.")
            if not await self.runtime.session.is_authenticated(target):
                raise RuntimeError("Complete Waterloo SSO/MFA before syncing applications.")
            last_job_update_at = self.runtime.snapshot.last_job_update_at
            self.runtime.snapshot.status = "syncing_applications"
            self.runtime.snapshot.message = "Opening Total Submitted applications…"
            self.runtime.snapshot.browser_open = True
            self.runtime.snapshot.started_at = datetime.now(UTC).isoformat()
            self.runtime.snapshot.finished_at = None
            self.runtime.snapshot.last_job_update_at = last_job_update_at
            self.runtime.snapshot.application_new_job_count = 0
            self.runtime.snapshot.tracker_synced_count = 0
            self.runtime.snapshot.application_failed_count = 0
            self.runtime.active_task = asyncio.create_task(
                self._sync_applications(target, sync_application)
            )
            return self.runtime.snapshot.payload()

    async def _sync_applications(
        self,
        target: dict[str, Any],
        sync_application: ApplicationSync,
    ) -> None:
        try:
            self.runtime.snapshot.page_url = WATERLOOWORKS_APPLICATIONS_URL
            raw_applications = await self.reader.read_applications(
                target, on_reading=self._report_reading
            )
            stored, counts, storage_errors = await asyncio.to_thread(
                self.runtime.repository.store_applications, raw_applications
            )
            tracker_errors: list[str] = []
            synced = 0
            for index, job in enumerate(stored, start=1):
                if index == 1 or index % 10 == 0:
                    self.runtime.snapshot.message = (
                        f"Adding submitted applications to Tracker: {index}/{len(stored)}…"
                    )
                try:
                    await sync_application(job)
                    synced += 1
                except Exception as error:
                    tracker_errors.append(f"{job.get('source_job_id')}: {error}")
            failures = counts["failed"] + len(tracker_errors)
            (
                self.runtime.snapshot.application_count,
                self.runtime.snapshot.unique_job_count,
                self.runtime.snapshot.last_job_update_at,
            ) = await asyncio.gather(
                asyncio.to_thread(self.runtime.repository.count_applications),
                asyncio.to_thread(self.runtime.repository.count_jobs),
                asyncio.to_thread(self.runtime.repository.latest_job_update_at),
            )
            self.runtime.snapshot.application_new_job_count = counts["new_jobs"]
            self.runtime.snapshot.tracker_synced_count = synced
            self.runtime.snapshot.application_failed_count = failures
            self.runtime.snapshot.finished_at = datetime.now(UTC).isoformat()
            if synced:
                self.runtime.snapshot.last_tracker_update_at = self.runtime.snapshot.finished_at
            self.runtime.snapshot.status = "partial" if failures else "completed"
            detail_note = (
                f" {counts['detail_failures']} descriptions were unavailable."
                if counts["detail_failures"]
                else ""
            )
            self.runtime.snapshot.message = (
                f"{failures} application sync failures.{detail_note}" if failures else ""
            )
            if storage_errors or tracker_errors:
                self.runtime.snapshot.message += (
                    " " + "; ".join([*storage_errors, *tracker_errors][:2])[:400]
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.runtime.snapshot.status = "failed"
            self.runtime.snapshot.message = str(error).splitlines()[0][:500]
            self.runtime.snapshot.finished_at = datetime.now(UTC).isoformat()
