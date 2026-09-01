"""API routes for ATS resume review and PDF extraction."""

from __future__ import annotations

from fastapi import APIRouter

from wecanfindintern.ats.commentary import (
    generate_ats_score_commentary,
    generate_job_match_commentary,
)
from wecanfindintern.ats.models import (
    AtsMatchRequest,
    AtsScoreCommentaryRequest,
    AtsScoreCommentaryResponse,
    JobMatchCommentaryRequest,
    JobMatchCommentaryResponse,
    JobMatchResult,
    ParsingReadinessResult,
    ResumeAtsScoreRequest,
)
from wecanfindintern.ats.service import (
    generate_ats_match,
    generate_resume_ats_score,
)

ats_router = APIRouter(prefix="/api/v1/ats", tags=["ATS Resume Review"])


@ats_router.post("/score", response_model=ParsingReadinessResult)
def run_resume_ats_score(payload: ResumeAtsScoreRequest):
    """Evaluate whether a resume can be parsed reliably by an ATS."""
    return generate_resume_ats_score(payload.resume_text)


@ats_router.post("/score/commentary", response_model=AtsScoreCommentaryResponse)
def run_resume_ats_score_commentary(payload: AtsScoreCommentaryRequest):
    """Generate qualitative feedback without changing the deterministic score."""
    return generate_ats_score_commentary(
        resume_text=payload.resume_text,
        diagnostic=payload.diagnostic,
        provider=payload.provider,
        model_name=payload.model_name,
        api_key=payload.api_key,
        api_base=payload.api_base,
    )


@ats_router.post("/match", response_model=JobMatchResult)
def run_ats_match(payload: AtsMatchRequest):
    """Evaluate resume evidence against one target job description."""
    return generate_ats_match(payload.resume_text, payload.job_description)


@ats_router.post("/match/commentary", response_model=JobMatchCommentaryResponse)
def run_job_match_commentary(payload: JobMatchCommentaryRequest):
    """Generate qualitative job-match feedback without changing the score."""
    return generate_job_match_commentary(
        resume_text=payload.resume_text,
        job_description=payload.job_description,
        diagnostic=payload.diagnostic,
        provider=payload.provider,
        model_name=payload.model_name,
        api_key=payload.api_key,
        api_base=payload.api_base,
    )
