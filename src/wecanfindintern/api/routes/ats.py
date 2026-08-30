"""API routes for ATS resume review and PDF extraction."""

from __future__ import annotations

import io
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile
from pypdf import PdfReader

from wecanfindintern.ats.models import AtsReviewRequest, AtsReviewResponse
from wecanfindintern.ats.parsing_readiness import score_parsing_readiness
from wecanfindintern.ats.service import generate_ats_review
from wecanfindintern.profile.security import extract_text_pdf_plain

ats_router = APIRouter(prefix="/api/v1/ats", tags=["ATS Resume Review"])


@ats_router.post("/extract-pdf")
async def extract_pdf(file: Annotated[UploadFile, File()]):
    """Extract plain text from uploaded PDF resume."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    try:
        content = await file.read()
        text = extract_text_pdf_plain(file.filename, file.content_type, content)
        reader = PdfReader(io.BytesIO(content), strict=True)
        page_texts = [(page.extract_text() or "") for page in reader.pages]
        readiness = score_parsing_readiness(text, page_texts=page_texts)
        return {
            "ok": True,
            "text": text,
            "filename": file.filename,
            "parsing_readiness": readiness.model_dump(mode="json"),
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="PDF extraction failed.") from exc


@ats_router.post("/review", response_model=AtsReviewResponse)
def run_ats_review(payload: AtsReviewRequest):
    """Run ATS keyword and qualifications match review."""
    return generate_ats_review(
        resume_text=payload.resume_text,
        job_description=payload.job_description,
        provider=payload.provider,
        model_name=payload.model_name,
        api_key=payload.api_key,
        api_base=payload.api_base,
    )
