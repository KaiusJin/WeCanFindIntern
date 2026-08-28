"""API routes for Cover Letter generation and export."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from wecanfindintern.cover_letter.export import export_docx, export_pdf
from wecanfindintern.cover_letter.models import (
    CoverLetterExportRequest,
    CoverLetterRequest,
    CoverLetterResponse,
)
from wecanfindintern.cover_letter.service import generate_cover_letter

cover_letter_router = APIRouter(prefix="/api/v1/cover-letter", tags=["Cover Letter Generator"])


@cover_letter_router.post("/generate", response_model=CoverLetterResponse)
def run_cover_letter_generation(payload: CoverLetterRequest):
    """Generate hyper-personalized cover letter."""
    return generate_cover_letter(
        resume_text=payload.resume_text,
        job_description=payload.job_description,
        user_info=payload.user_info,
        date_str=payload.date_str,
        provider=payload.provider,
        model_name=payload.model_name,
        api_key=payload.api_key,
    )


@cover_letter_router.post("/export")
def cover_letter_export(payload: CoverLetterExportRequest):
    """Export formatted cover letter as docx or pdf."""
    fmt = payload.format.lower()
    if fmt == "docx":
        docx_bytes = export_docx(payload.body, payload.user_info)
        return Response(
            content=docx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": 'attachment; filename="Cover_Letter.docx"'},
        )
    elif fmt == "pdf":
        pdf_bytes = export_pdf(payload.body, payload.user_info)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="Cover_Letter.pdf"'},
        )
    else:
        raise HTTPException(status_code=400, detail="Unsupported export format.")
