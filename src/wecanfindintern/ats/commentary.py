"""AI commentary layered on top of the deterministic ATS score."""

from __future__ import annotations

import logging

from pydantic import ValidationError

from wecanfindintern.ats.models import (
    AtsScoreCommentary,
    AtsScoreCommentaryResponse,
    JobMatchCommentary,
    JobMatchCommentaryResponse,
    JobMatchResult,
    ParsingReadinessResult,
)
from wecanfindintern.llm.gateway import (
    LLMError,
    complete_json,
    json_response_format,
    resolve_api_key,
)
from wecanfindintern.llm.prompts.ats import (
    build_ats_score_commentary_prompt,
    build_job_match_commentary_prompt,
)

logger = logging.getLogger(__name__)


def generate_ats_score_commentary(
    *,
    resume_text: str,
    diagnostic: ParsingReadinessResult,
    provider: str,
    model_name: str | None,
    api_key: str | None,
    api_base: str | None,
) -> AtsScoreCommentaryResponse:
    """Generate grounded feedback without allowing the model to alter the score."""

    try:
        resolved_key = resolve_api_key(provider, api_key)
        result = complete_json(
            provider=provider,
            model_name=model_name,
            api_key=resolved_key,
            api_base=api_base,
            system_prompt=(
                "You are a resume advisor specializing in ATS readability. Return "
                "valid JSON only. Treat the supplied score and category breakdown as "
                "authoritative; provide qualitative feedback, never a replacement score."
            ),
            user_prompt=build_ats_score_commentary_prompt(resume_text, diagnostic),
            response_format=json_response_format(provider),
        )
        commentary = AtsScoreCommentary.model_validate(result.data)
        return AtsScoreCommentaryResponse(
            ok=True,
            commentary=commentary,
            usage=result.usage,
        )
    except (LLMError, ValidationError, TypeError, ValueError) as error:
        logger.warning(
            "ATS score commentary could not be generated: provider=%s model=%s error=%s",
            provider,
            model_name,
            error,
        )
        return AtsScoreCommentaryResponse(
            ok=False,
            error=(
                "AI feedback could not be generated right now. "
                "Your ATS score is still available."
            ),
        )


def generate_job_match_commentary(
    *,
    resume_text: str,
    job_description: str,
    diagnostic: JobMatchResult,
    provider: str,
    model_name: str | None,
    api_key: str | None,
    api_base: str | None,
) -> JobMatchCommentaryResponse:
    """Generate grounded job-match feedback without altering the score."""

    try:
        resolved_key = resolve_api_key(provider, api_key)
        result = complete_json(
            provider=provider,
            model_name=model_name,
            api_key=resolved_key,
            api_base=api_base,
            system_prompt=(
                "You are a career advisor assessing resume-to-role alignment. Return valid "
                "JSON only. Treat the supplied score and breakdown as authoritative; provide "
                "qualitative feedback, never a replacement score or hiring prediction."
            ),
            user_prompt=build_job_match_commentary_prompt(
                resume_text,
                job_description,
                diagnostic,
            ),
            response_format=json_response_format(provider),
        )
        commentary = JobMatchCommentary.model_validate(result.data)
        return JobMatchCommentaryResponse(
            ok=True,
            commentary=commentary,
            usage=result.usage,
        )
    except (LLMError, ValidationError, TypeError, ValueError) as error:
        logger.warning(
            "Job match commentary could not be generated: provider=%s model=%s error=%s",
            provider,
            model_name,
            error,
        )
        return JobMatchCommentaryResponse(
            ok=False,
            error=(
                "AI feedback could not be generated right now. "
                "Your job match score is still available."
            ),
        )
