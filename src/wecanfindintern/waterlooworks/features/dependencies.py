"""Local HTTP access policy shared by WaterlooWorks feature endpoints."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from wecanfindintern.waterlooworks.service import WaterlooWorksService


def get_service(request: Request) -> WaterlooWorksService:
    if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="WaterlooWorks import is local-only.")
    return request.app.state.waterlooworks


ServiceDep = Annotated[WaterlooWorksService, Depends(get_service)]
