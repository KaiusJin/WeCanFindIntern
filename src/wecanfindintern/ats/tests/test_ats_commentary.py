"""Tests for AI commentary layered on deterministic ATS diagnostics."""

from wecanfindintern.ats import commentary as commentary_service
from wecanfindintern.ats.match_scoring import score_job_match
from wecanfindintern.llm.gateway import LLMResult
from wecanfindintern.llm.prompts.ats import build_job_match_commentary_prompt

RESUME = """
Alex Chen
Software Developer Intern
Built Python FastAPI services and PostgreSQL data pipelines.
Skills: Python, SQL, Docker, AWS, React, TypeScript
"""

JOB_DESCRIPTION = """
Software Developer Intern
Required: Python, SQL, Docker, and AWS.
Preferred: React and TypeScript.
"""


def test_job_match_commentary_is_grounded_in_supplied_diagnostic():
    diagnostic = score_job_match(RESUME, JOB_DESCRIPTION)
    prompt = build_job_match_commentary_prompt(
        RESUME,
        JOB_DESCRIPTION,
        diagnostic,
    )

    assert str(diagnostic.score) in prompt
    assert "Do not recalculate or contradict the supplied score" in prompt
    assert "application or interview probabilities" in prompt


def test_job_match_commentary_returns_structured_feedback(monkeypatch):
    diagnostic = score_job_match(RESUME, JOB_DESCRIPTION)

    def fake_complete_json(**kwargs):
        assert "replacement score or hiring prediction" in kwargs["system_prompt"]
        return LLMResult(
            data={
                "summary": "The resume aligns well with the core technical requirements.",
                "strengths": ["Python and SQL evidence directly supports required skills."],
                "improvements": ["Add a quantified Docker deployment outcome."],
            },
            usage={"total_tokens": 100},
            provider="Gemini",
            model="gemini-test",
        )

    monkeypatch.setattr(commentary_service, "complete_json", fake_complete_json)
    result = commentary_service.generate_job_match_commentary(
        resume_text=RESUME,
        job_description=JOB_DESCRIPTION,
        diagnostic=diagnostic,
        provider="Gemini",
        model_name="gemini-test",
        api_key="test-key",
        api_base=None,
    )

    assert result.ok is True
    assert result.commentary is not None
    assert result.commentary.strengths
    assert result.commentary.improvements
    assert result.usage == {"total_tokens": 100}


def test_job_match_commentary_failure_preserves_score_availability():
    diagnostic = score_job_match(RESUME, JOB_DESCRIPTION)
    result = commentary_service.generate_job_match_commentary(
        resume_text=RESUME,
        job_description=JOB_DESCRIPTION,
        diagnostic=diagnostic,
        provider="Gemini",
        model_name="gemini-test",
        api_key=None,
        api_base=None,
    )

    assert result.ok is False
    assert result.commentary is None
    assert "job match score is still available" in result.error
