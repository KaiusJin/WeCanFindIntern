"""Unit tests for ATS resume evaluation module."""

from wecanfindintern.ats.models import AtsReviewRequest, AtsReviewResponse
from wecanfindintern.ats.service import match_level
from wecanfindintern.llm.client import clean_json_text, resolve_api_key


def test_clean_json_text():
    markdown_json = '```json\n{"score": 85, "summary": "Great match"}\n```'
    assert clean_json_text(markdown_json) == '{"score": 85, "summary": "Great match"}'

    raw_text = 'Some prefix {"key": "val"} some suffix'
    assert clean_json_text(raw_text) == '{"key": "val"}'


def test_match_level():
    assert "High" in match_level(85)
    assert "Medium" in match_level(65)
    assert "Low" in match_level(40)


def test_ats_models_instantiation():
    ats_resp = AtsReviewResponse(
        ok=True,
        score=90,
        level="High",
        summary="Looks good",
        strengths=["Python", "FastAPI"],
        gaps=[],
        suggestions=[],
    )
    assert ats_resp.score == 90
    assert len(ats_resp.strengths) == 2


def test_missing_api_key_or_model_error():
    from wecanfindintern.ats.service import generate_ats_review

    res = generate_ats_review(
        resume_text="Some resume",
        job_description="Some JD",
        provider="Gemini",
        model_name="gemini-3.7-flash",
        api_key="",
    )
    assert not res.ok
    assert "Missing Gemini API key" in res.error
