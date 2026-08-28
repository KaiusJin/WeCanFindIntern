"""API routes for ATS resume review and PDF extraction."""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from wecanfindintern.ats.models import AtsReviewRequest, AtsReviewResponse
from wecanfindintern.ats.pdf import extract_text_from_pdf
from wecanfindintern.ats.service import generate_ats_review

ats_router = APIRouter(prefix="/api/v1/ats", tags=["ATS Resume Review"])


@ats_router.post("/extract-pdf")
async def extract_pdf(file: UploadFile = File(...)):
    """Extract plain text from uploaded PDF resume."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    try:
        content = await file.read()
        text = extract_text_from_pdf(content)
        return {"ok": True, "text": text, "filename": file.filename}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@ats_router.post("/review", response_model=AtsReviewResponse)
def run_ats_review(payload: AtsReviewRequest):
    """Run ATS keyword and qualifications match review."""
    return generate_ats_review(
        resume_text=payload.resume_text,
        job_description=payload.job_description,
        provider=payload.provider,
        model_name=payload.model_name,
        api_key=payload.api_key,
    )
