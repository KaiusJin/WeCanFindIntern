"""Mount the HTTP entry points owned by WaterlooWorks feature slices."""

from fastapi import APIRouter

from wecanfindintern.waterlooworks.features.browse_jobs.api import router as jobs_router
from wecanfindintern.waterlooworks.features.collect_jobs.api import router as collection_router
from wecanfindintern.waterlooworks.features.connection.api import router as connection_router
from wecanfindintern.waterlooworks.features.sync_applications.api import (
    router as applications_router,
)

waterlooworks_router = APIRouter(prefix="/api/v1/waterlooworks", tags=["WaterlooWorks"])
waterlooworks_router.include_router(connection_router)
waterlooworks_router.include_router(collection_router)
waterlooworks_router.include_router(applications_router)
waterlooworks_router.include_router(jobs_router)
