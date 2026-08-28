"""Unit tests for Cover Letter generation and export module."""

from wecanfindintern.cover_letter.export import export_docx, export_pdf
from wecanfindintern.cover_letter.models import (
    CoverLetterExportRequest,
    CoverLetterRequest,
    CoverLetterResponse,
    UserProfile,
)
from wecanfindintern.cover_letter.service import generate_cover_letter


def test_cover_letter_exports():
    user = UserProfile(
        full_name="Jane Doe",
        email="jane@example.com",
        phone="555-123-4567",
        linkedin="linkedin.com/in/janedoe",
        address="San Francisco, CA",
    )
    body = "Dear Hiring Manager,\n\nI am writing to express my strong interest in the Software Engineering Intern role."

    docx_bytes = export_docx(body, user)
    assert len(docx_bytes) > 0

    pdf_bytes = export_pdf(body, user)
    assert len(pdf_bytes) > 0


def test_cover_letter_missing_model():
    user = UserProfile(full_name="Jane Doe")
    resp = generate_cover_letter(
        resume_text="Resume content",
        job_description="JD content",
        user_info=user,
        provider="Gemini",
        model_name="",
        api_key="AIzaSy_fake",
    )
    assert not resp.ok
    assert "No AI model selected" in resp.error


def test_cover_letter_missing_api_key():
    user = UserProfile(full_name="Jane Doe")
    resp = generate_cover_letter(
        resume_text="Resume content",
        job_description="JD content",
        user_info=user,
        provider="DeepSeek",
        model_name="deepseek-chat",
        api_key=None,
    )
    assert not resp.ok
    assert "Missing DeepSeek API key" in resp.error
