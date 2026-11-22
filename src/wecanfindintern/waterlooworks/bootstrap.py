"""Composition root for the local WaterlooWorks bounded context."""

from __future__ import annotations

import os

from wecanfindintern.waterlooworks.application.runtime import WaterlooWorksRuntime
from wecanfindintern.waterlooworks.domain.boards import WATERLOOWORKS_BOARDS
from wecanfindintern.waterlooworks.infrastructure.browser.application_reader import (
    ChromeApplicationReader,
)
from wecanfindintern.waterlooworks.infrastructure.browser.job_reader import ChromeJobReader
from wecanfindintern.waterlooworks.infrastructure.browser.session import (
    ChromeSession,
    find_chrome_binary,
)
from wecanfindintern.waterlooworks.infrastructure.persistence.repository import (
    WaterlooWorksRepository,
)
from wecanfindintern.waterlooworks.infrastructure.settings import (
    waterlooworks_database_path,
    waterlooworks_profile_path,
)
from wecanfindintern.waterlooworks.service import WaterlooWorksService


def create_waterlooworks_service() -> WaterlooWorksService:
    session = ChromeSession(
        profile_dir=waterlooworks_profile_path(),
        start_url=os.getenv("WATERLOOWORKS_URL", WATERLOOWORKS_BOARDS[0][1]),
        chrome_binary=os.getenv("WATERLOOWORKS_CHROME_BINARY") or find_chrome_binary(),
    )
    runtime = WaterlooWorksRuntime(
        session=session,
        repository=WaterlooWorksRepository(waterlooworks_database_path()),
    )
    return WaterlooWorksService(
        runtime,
        job_reader=ChromeJobReader(session),
        application_reader=ChromeApplicationReader(session),
    )
