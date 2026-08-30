"""Bounded LLM adjustments for deterministic job recommendations.

The model never owns the final score and cannot introduce or remove candidates.
It may only suggest a small, evidence-backed adjustment for the deterministic
shortlist. Invalid, unsupported, or failed responses have no ranking effect.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from wecanfindintern.llm.gateway import complete_json, json_response_format

if TYPE_CHECKING:  # avoid a runtime import cycle with agent.tools
    from wecanfindintern.agent.tools import LlmConfig

logger = logging.getLogger(__name__)

MAX_RERANK_CANDIDATES = 15
MAX_ABS_ADJUSTMENT = 5
EXCERPT_CHARS = 320


@dataclass(frozen=True, slots=True)
class RerankOutcome:
    adjustments: dict[int, int]
    reasons: dict[int, str]


def _candidate_line(index: int, candidate: dict[str, Any]) -> str:
    excerpt = ""
    description = candidate.get("description") or ""
    if description:
        excerpt = " | " + " ".join(description.split())[:EXCERPT_CHARS]
    deadline = candidate.get("application_deadline") or "not specified"
    return (
        f"{index}. {candidate.get('title') or 'Untitled'} at "
        f"{candidate.get('company') or 'unknown'} | "
        f"{candidate.get('location') or 'unknown location'} | "
        f"work mode {candidate.get('work_mode') or 'unknown'} | "
        f"skills: {', '.join(candidate.get('skill_tags') or []) or 'none'} | "
        f"deadline: {deadline}{excerpt}"
    )


def rerank_with_llm(
    *,
    llm_config: LlmConfig,
    candidates: list[dict[str, Any]],
    profile_summary: dict[str, Any],
    preferences: dict[str, str],
    language: str = "match the user's language",
) -> RerankOutcome | None:
    """Re-rank candidates; returns None on any failure (caller degrades)."""

    if len(candidates) < 2:
        return None
    shortlist = candidates[:MAX_RERANK_CANDIDATES]
    system_prompt = (
        "You are a conservative job-match reviewer. A deterministic ranker has "
        "already applied hard constraints and scored the candidates. Do not invent "
        "facts, do not create a new fit score, and do not remove candidates. You may "
        f"suggest an integer adjustment from {-MAX_ABS_ADJUSTMENT} to "
        f"{MAX_ABS_ADJUSTMENT} only when the supplied evidence supports it. Write "
        f"reasons in {language}. Output ONLY JSON: "
        '{"adjustments": [{"candidate": 0, "delta": 2, '
        '"reason": "one evidence-based sentence"}]}. '
        "Omit candidates that need no adjustment."
    )
    user_prompt = (
        "## Candidate profile\n"
        f"skills: {', '.join(profile_summary.get('skills') or []) or 'none'}\n"
        f"education stage: {profile_summary.get('education_stage') or 'unknown'}\n"
        f"target roles: {profile_summary.get('target_roles') or 'not specified'}\n"
        f"preferences: {preferences or 'none'}\n\n"
        "## Job candidates\n"
        + "\n".join(
            _candidate_line(index, candidate)
            for index, candidate in enumerate(shortlist)
        )
    )
    try:
        result = complete_json(
            provider=llm_config.provider,
            model_name=llm_config.model_name,
            api_key=llm_config.api_key,
            api_base=llm_config.api_base,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_format=json_response_format(llm_config.provider),
            timeout_seconds=8.0,
            max_retries=0,
        )
        data = result.data
    except Exception as error:
        logger.warning("Recommendation rerank failed: %s", error)
        return None
    if not isinstance(data, dict):
        return None

    raw_adjustments = data.get("adjustments")
    if not isinstance(raw_adjustments, list):
        return None
    adjustments: dict[int, int] = {}
    reasons: dict[int, str] = {}
    seen: set[int] = set()
    for value in raw_adjustments:
        if not isinstance(value, dict):
            continue
        try:
            index = int(value.get("candidate"))
            delta = int(value.get("delta"))
        except (TypeError, ValueError):
            continue
        reason = value.get("reason")
        if (
            isinstance(value.get("candidate"), bool)
            or not 0 <= index < len(shortlist)
            or index in seen
            or not -MAX_ABS_ADJUSTMENT <= delta <= MAX_ABS_ADJUSTMENT
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            continue
        seen.add(index)
        adjustments[index] = delta
        reasons[index] = reason.strip()[:240]
    if not adjustments:
        return None
    return RerankOutcome(adjustments=adjustments, reasons=reasons)
