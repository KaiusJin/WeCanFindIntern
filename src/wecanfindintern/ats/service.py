"""Transparent ATS-style resume diagnostics and job matching."""

from __future__ import annotations

from wecanfindintern.ats.match_scoring import score_job_match
from wecanfindintern.ats.models import AtsReviewResponse
from wecanfindintern.ats.parsing_readiness import score_parsing_readiness


def match_level(score: int) -> str:
    if score >= 80:
        return "High Match"
    if score >= 55:
        return "Medium Match"
    return "Low Match"


def generate_ats_review(
    resume_text: str,
    job_description: str,
    provider: str = "Gemini",
    model_name: str | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
) -> AtsReviewResponse:
    """Compute both scores in code; model settings cannot influence results."""

    del provider, model_name, api_key, api_base
    if not resume_text.strip():
        return AtsReviewResponse(ok=False, error="Resume text cannot be empty.")
    if not job_description.strip():
        return AtsReviewResponse(ok=False, error="Job description cannot be empty.")
    return AtsReviewResponse(
        ok=True,
        parsing_readiness=score_parsing_readiness(resume_text),
        job_match=score_job_match(resume_text, job_description),
        usage={"scoring": "deterministic", "version": "ats-match.v1"},
    )
