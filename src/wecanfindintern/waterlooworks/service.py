"""Public application facade; feature slices own the use-case implementations."""

from __future__ import annotations

from typing import Any

from wecanfindintern.waterlooworks.application.ports import (
    ApplicationReader,
    ApplicationSync,
    JobReader,
)
from wecanfindintern.waterlooworks.application.runtime import WaterlooWorksRuntime
from wecanfindintern.waterlooworks.features.browse_jobs.queries import JobQueries
from wecanfindintern.waterlooworks.features.collect_jobs.use_case import CollectJobs
from wecanfindintern.waterlooworks.features.connection.use_case import ConnectWaterlooWorks
from wecanfindintern.waterlooworks.features.sync_applications.use_case import SyncApplications


class WaterlooWorksService:
    def __init__(
        self,
        runtime: WaterlooWorksRuntime,
        *,
        job_reader: JobReader,
        application_reader: ApplicationReader,
    ) -> None:
        self.runtime = runtime
        self.connection = ConnectWaterlooWorks(runtime)
        self.collection = CollectJobs(runtime, job_reader)
        self.applications = SyncApplications(runtime, application_reader)
        self.jobs = JobQueries(runtime.repository)

    async def launch(self) -> dict[str, Any]:
        return await self.connection.launch()

    async def get_status(self) -> dict[str, Any]:
        return await self.connection.get_status()

    async def start_collection(self) -> dict[str, Any]:
        return await self.collection.execute()

    async def start_application_sync(self, sync_application: ApplicationSync) -> dict[str, Any]:
        return await self.applications.execute(sync_application)

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
        return await self.jobs.list_jobs(
            board=board,
            boards=boards,
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
        return await self.jobs.get_job(source_job_id)

    async def close(self) -> None:
        await self.runtime.close()
