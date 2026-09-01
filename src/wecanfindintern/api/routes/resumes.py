"""Shared resume extraction endpoints used by all resume-consuming sections."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, UploadFile

from wecanfindintern.api.resume_upload import extract_pdf_upload

resumes_router = APIRouter(prefix="/api/v1/resumes", tags=["Resumes"])

@resumes_router.post("/extract-pdf")
async def extract_pdf(file: Annotated[UploadFile, File()]) -> dict:
    return await extract_pdf_upload(file)
