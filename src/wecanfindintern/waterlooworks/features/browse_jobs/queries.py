"""Query locally collected WaterlooWorks jobs."""

from __future__ import annotations

import asyncio
from typing import Any

from wecanfindintern.waterlooworks.application.ports import JobRepository
from wecanfindintern.waterlooworks.domain.boards import WATERLOOWORKS_BOARDS


class JobQueries:
    def __init__(self, repository: JobRepository) -> None:
        self.repository = repository

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
        allowed_boards = {name for name, _ in WATERLOOWORKS_BOARDS} | {"applications"}
        selected_boards = list(dict.fromkeys([*(boards or []), *([board] if board else [])]))
        unknown_boards = [value for value in selected_boards if value not in allowed_boards]
        if unknown_boards:
            raise RuntimeError(f"Unknown WaterlooWorks board: {unknown_boards[0]}")
        return await asyncio.to_thread(
            self.repository.list_jobs,
            boards=selected_boards,
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
        return await asyncio.to_thread(self.repository.get_job, source_job_id)
