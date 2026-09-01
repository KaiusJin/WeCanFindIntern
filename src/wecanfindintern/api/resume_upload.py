"""Shared FastAPI adapter for safe PDF resume extraction."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, UploadFile

from wecanfindintern.ats.parsing_readiness import score_parsing_readiness
from wecanfindintern.profile.parser import parse_resume_text
from wecanfindintern.profile.security import MAX_PDF_BYTES, extract_pdf_text


async def extract_pdf_upload(file: UploadFile) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    try:
        content = await file.read(MAX_PDF_BYTES + 1)
        extracted = extract_pdf_text(file.filename, file.content_type, content)
        readiness = score_parsing_readiness(
            extracted.text, page_texts=list(extracted.page_texts)
        )
        basics = parse_resume_text(extracted.text).basics
        return {
            "ok": True,
            "text": extracted.text,
            "filename": file.filename,
            "contact_information": {
                "full_name": basics.full_name,
                "email": basics.email or "",
                "phone": basics.phone or "",
                "linkedin": (
                    basics.linkedin_url
                    or basics.github_url
                    or basics.portfolio_url
                    or ""
                ),
            },
            "parsing_readiness": readiness.model_dump(mode="json"),
        }
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail="PDF extraction failed.") from error
