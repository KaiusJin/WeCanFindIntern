"""Connection status and browser launch endpoints."""

from typing import Any

from fastapi import APIRouter, HTTPException

from wecanfindintern.waterlooworks.features.dependencies import ServiceDep

router = APIRouter()


@router.get("/status")
async def status(service: ServiceDep) -> dict[str, Any]:
    return await service.get_status()


@router.post("/launch")
async def launch(service: ServiceDep) -> dict[str, Any]:
    try:
        return await service.launch()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
