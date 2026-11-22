"""Start a local job collection."""

from typing import Any

from fastapi import APIRouter, HTTPException

from wecanfindintern.waterlooworks.features.dependencies import ServiceDep

router = APIRouter()


@router.post("/collect", status_code=202)
async def collect(service: ServiceDep) -> dict[str, Any]:
    try:
        return await service.start_collection()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
