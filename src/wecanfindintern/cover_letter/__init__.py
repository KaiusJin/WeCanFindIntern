"""Cover letter generation and export module."""

from wecanfindintern.cover_letter.export import export_docx, export_pdf
from wecanfindintern.cover_letter.models import (
    CoverLetterContact,
    CoverLetterExportRequest,
    CoverLetterRequest,
    CoverLetterResponse,
    UserProfile,
)
from wecanfindintern.cover_letter.service import generate_cover_letter

__all__ = [
    "CoverLetterExportRequest",
    "CoverLetterContact",
    "CoverLetterRequest",
    "CoverLetterResponse",
    "UserProfile",
    "export_docx",
    "export_pdf",
    "generate_cover_letter",
]
