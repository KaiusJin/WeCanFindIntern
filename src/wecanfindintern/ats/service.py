"""Transparent ATS-style resume diagnostics and job matching."""

from __future__ import annotations

from wecanfindintern.ats.match_scoring import score_job_match
from wecanfindintern.ats.models import JobMatchResult, ParsingReadinessResult
from wecanfindintern.ats.parsing_readiness import score_parsing_readiness


def generate_resume_ats_score(resume_text: str) -> ParsingReadinessResult:
    """Score resume parsing readiness independently from any job target."""

    return score_parsing_readiness(resume_text)


def generate_ats_match(
    resume_text: str,
    job_description: str,
) -> JobMatchResult:
    """Score resume evidence against a specific job description."""

    return score_job_match(resume_text, job_description)
