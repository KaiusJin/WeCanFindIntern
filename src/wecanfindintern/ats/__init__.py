"""ATS resume review module."""

from wecanfindintern.ats.commentary import (
    generate_ats_score_commentary,
    generate_job_match_commentary,
)
from wecanfindintern.ats.models import (
    AtsMatchRequest,
    AtsScoreCommentary,
    AtsScoreCommentaryRequest,
    AtsScoreCommentaryResponse,
    JobMatchCommentary,
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

__all__ = [
    "AtsMatchRequest",
    "AtsScoreCommentary",
    "AtsScoreCommentaryRequest",
    "AtsScoreCommentaryResponse",
    "JobMatchCommentary",
    "JobMatchCommentaryRequest",
    "JobMatchCommentaryResponse",
    "JobMatchResult",
    "ParsingReadinessResult",
    "ResumeAtsScoreRequest",
    "generate_ats_match",
    "generate_ats_score_commentary",
    "generate_job_match_commentary",
    "generate_resume_ats_score",
]
