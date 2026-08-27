"""Unit tests for Career AI modules (PDF extraction, models, clean_json, exports)."""

import io
from wecanfindintern.career_ai.export import export_docx, export_latex, export_pdf
from wecanfindintern.career_ai.models import (
    AtsReviewResponse,
    CoverLetterResponse,
    InterviewQuestionItem,
    InterviewQuestionsResponse,
    UserProfile,
)
from wecanfindintern.career_ai.service import clean_json_text, match_level


def test_clean_json_text():
    markdown_json = '```json\n{"score": 85, "summary": "Great match"}\n```'
    assert clean_json_text(markdown_json) == '{"score": 85, "summary": "Great match"}'

    raw_text = 'Some prefix {"key": "val"} some suffix'
    assert clean_json_text(raw_text) == '{"key": "val"}'


def test_match_level():
    assert "高匹配" in match_level(85)
    assert "中匹配" in match_level(65)
    assert "基础匹配" in match_level(40)


def test_models_validation():
    ats_resp = AtsReviewResponse(
        ok=True,
        score=90,
        level="高匹配",
        summary="Strong candidate",
        strengths=["Python", "FastAPI"],
        gaps=["Docker"],
        suggestions=["Highlight Docker experience"],
    )
    assert ats_resp.score == 90
    assert len(ats_resp.strengths) == 2

    q_resp = InterviewQuestionsResponse(
        ok=True,
        questions=[
            InterviewQuestionItem(
                id=1,
                category="icebreaker",
                category_label="Icebreaker",
                question="Tell me about yourself.",
            )
        ],
    )
    assert len(q_resp.questions) == 1


def test_exports():
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

    tex_str = export_latex(body, user)
    assert "Jane Doe" in tex_str
    assert "\\begin{document}" in tex_str
