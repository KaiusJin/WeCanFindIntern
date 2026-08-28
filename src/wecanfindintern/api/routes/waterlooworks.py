"""Local-only WaterlooWorks browser collection endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from wecanfindintern.waterlooworks.service import WaterlooWorksService

waterlooworks_router = APIRouter(
    prefix="/api/v1/waterlooworks",
    tags=["WaterlooWorks"],
)


def get_service(request: Request) -> WaterlooWorksService:
    if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="WaterlooWorks import is local-only.")
    return request.app.state.waterlooworks


ServiceDep = Annotated[WaterlooWorksService, Depends(get_service)]


@waterlooworks_router.get("/status")
async def status(service: ServiceDep) -> dict[str, Any]:
    return await service.get_status()


@waterlooworks_router.post("/launch")
async def launch(service: ServiceDep) -> dict[str, Any]:
    try:
        return await service.launch()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@waterlooworks_router.post("/collect", status_code=202)
async def collect(service: ServiceDep) -> dict[str, Any]:
    try:
        return await service.start_collection()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@waterlooworks_router.get("/jobs")
async def list_jobs(
    service: ServiceDep,
    board: str | None = Query(default=None, max_length=40),
    query: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    try:
        return await service.list_jobs(
            board=board,
            query=query,
            limit=limit,
            offset=offset,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
