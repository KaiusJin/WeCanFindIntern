"""Backward-compatible PDF text extraction entry point."""

from __future__ import annotations

from wecanfindintern.profile.security import extract_text_pdf_plain


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract validated plain text and ordinary links from PDF bytes."""
    return extract_text_pdf_plain("resume.pdf", "application/pdf", pdf_bytes)
