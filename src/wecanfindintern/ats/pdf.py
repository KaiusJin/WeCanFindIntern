"""PDF text extraction utilities for resumes."""

from __future__ import annotations

import io
from pypdf import PdfReader


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract text from uploaded PDF bytes."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text_parts = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(text_parts).strip()
    except Exception as exc:
        raise ValueError(f"Failed to extract text from PDF: {exc}") from exc
