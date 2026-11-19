"""Unit tests for Cover Letter generation and export module."""

import json

import wecanfindintern.cover_letter.service as cover_letter_service
from wecanfindintern.cover_letter.export import export_docx, export_pdf
from wecanfindintern.cover_letter.models import UserProfile
from wecanfindintern.cover_letter.service import generate_cover_letter
from wecanfindintern.llm.gateway import LLMResult


def test_cover_letter_exports():
    user = UserProfile(
        full_name="Jane Doe",
        email="jane@example.com",
        phone="555-123-4567",
        linkedin="linkedin.com/in/janedoe",
        address="San Francisco, CA",
    )
    body = (
        "Dear Hiring Manager,\n\nI am writing to express my strong interest "
        "in the Software Engineering Intern role."
    )

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


def test_cover_letter_requires_contact_details():
    result = generate_cover_letter(
        resume_text="Resume content",
        job_description="Job description",
        user_info=UserProfile(full_name="Jane Doe"),
        provider="Gemini",
        model_name="gemini-test",
        api_key="AIzaSy_fake",
    )
    assert not result.ok
    assert "Complete contact details" in result.error


def test_cover_letter_runs_writer_and_reviewer_ai(monkeypatch):
    responses = iter(
        [
            '{"cover_letter":"Dear Hiring Manager,\\n\\nI built APIs with Python.'
            '\\n\\nI also completed a data project.\\n\\nThank you for considering '
            'my application.\\n\\nSincerely,\\nJane Doe","hr_info":{}}',
            '{"approved":true,"summary":"All candidate claims are supported.",'
            '"issues":[],"unsupported_claims":[]}',
        ]
    )
    calls = []

    def fake_complete_json(**kwargs):
        calls.append(kwargs["user_prompt"])
        return LLMResult(
            data=json.loads(next(responses)),
            usage={},
            provider="Gemini",
            model="gemini-test",
        )

    monkeypatch.setattr(cover_letter_service, "complete_json", fake_complete_json)
    result = cover_letter_service.generate_cover_letter(
        resume_text="Jane built APIs with Python. Jane completed a data project.",
        job_description="We need Python and API experience.",
        user_info=UserProfile(
            full_name="Jane Doe",
            email="jane@example.com",
            phone="555-0100",
            linkedin="linkedin.com/in/jane",
        ),
        provider="Gemini",
        model_name="gemini-test",
        api_key="AIzaSy_fake",
    )
    assert result.ok is True
    assert result.review_approved is True
    assert len(calls) == 2
    assert "Reviewer AI" not in calls[0]
    assert "COVER LETTER" in calls[1]


def test_cover_letter_retries_writer_after_reviewer_rejection(monkeypatch):
    responses = iter(
        [
            '{"cover_letter":"draft one","hr_info":{}}',
            '{"approved":false,"summary":"Unsupported claim found.",'
            '"issues":["Remove unsupported claim."],"unsupported_claims":["Claim X"]}',
            '{"cover_letter":"draft two","hr_info":{}}',
            '{"approved":true,"summary":"Grounded after revision.",'
            '"issues":[],"unsupported_claims":[]}',
        ]
    )
    calls = []

    def fake_complete_json(**kwargs):
        calls.append(kwargs["user_prompt"])
        return LLMResult(
            data=json.loads(next(responses)),
            usage={},
            provider="Gemini",
            model="gemini-test",
        )

    monkeypatch.setattr(cover_letter_service, "complete_json", fake_complete_json)
    result = cover_letter_service.generate_cover_letter(
        resume_text="Supported resume evidence.",
        job_description="Target role requirements.",
        user_info=UserProfile(
            full_name="Jane Doe",
            email="jane@example.com",
            phone="555-0100",
            linkedin="linkedin.com/in/jane",
        ),
        provider="Gemini",
        model_name="gemini-test",
        api_key="AIzaSy_fake",
    )
    assert result.review_approved is True
    assert result.review_attempts == 2
    assert len(calls) == 4
    assert "Unsupported claim found." in calls[2]
