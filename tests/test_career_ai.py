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


def test_missing_api_key_or_model_error():
    from wecanfindintern.career_ai.service import (
        generate_ats_review,
        generate_interview_questions,
        analyze_interview_performance,
        generate_cover_letter,
    )

    # 1. Missing model error
    ats_no_model = generate_ats_review(
        resume_text="Some resume",
        job_description="Some JD",
        provider="Gemini",
        model_name=None,
        api_key="AIzaSyTestKey",
    )
    assert not ats_no_model.ok
    assert "No AI model selected" in ats_no_model.error

    # 2. Missing API key error
    ats_no_key = generate_ats_review(
        resume_text="Some resume",
        job_description="Some JD",
        provider="Gemini",
        model_name="gemini-2.5-flash",
        api_key=None,
    )
    assert not ats_no_key.ok
    assert "Missing Gemini API key" in ats_no_key.error

    # 3. Interview questions missing model
    iq_no_model = generate_interview_questions(
        job_description="Some JD",
        provider="DeepSeek",
        model_name=None,
        api_key="sk-test",
    )
    assert not iq_no_model.ok
    assert "No AI model selected" in iq_no_model.error

    # 4. Cover letter missing key
    cl_no_key = generate_cover_letter(
        resume_text="Resume",
        job_description="JD",
        user_info=UserProfile(),
        provider="OpenAI",
        model_name="gpt-4o-mini",
        api_key=None,
    )
    assert not cl_no_key.ok
    assert "Missing OpenAI API key" in cl_no_key.error

