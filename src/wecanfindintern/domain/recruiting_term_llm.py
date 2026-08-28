"""DeepSeek JSON-constrained fallback for recruiting season extraction."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from wecanfindintern.domain.recruiting_term import RecruitingTerm
from wecanfindintern.llm.gateway import LLMError, complete_json
from wecanfindintern.llm.prompts.recruiting_term import (
    build_recruiting_term_system_prompt,
)

logger = logging.getLogger(__name__)


class RecruitingTermLLMResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    found: bool
    season: Literal["winter", "spring", "summer", "fall"] | None
    year: int | None = Field(default=None, ge=2020, le=2099)
    confidence: float = Field(ge=0, le=1)
    evidence: str | None

    @model_validator(mode="after")
    def validate_found_fields(self):
        if self.found and (self.season is None or self.year is None or not self.evidence):
            raise ValueError("found results require season, year, and evidence")
        if not self.found and any(
            value is not None for value in (self.season, self.year, self.evidence)
        ):
            raise ValueError("not-found results must use null extraction fields")
        return self


@dataclass(frozen=True, slots=True)
class RecruitingTermLLMCall:
    extraction: RecruitingTerm | None
    model: str
    response_json: dict | None
    prompt_tokens: int | None
    completion_tokens: int | None
    error_type: str | None = None


def extract_recruiting_term_with_deepseek(
    context: str,
) -> RecruitingTermLLMCall:
    model = os.getenv("DEEPSEEK_TERM_MODEL", "deepseek-chat")
    if os.getenv("DEEPSEEK_TERM_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        return RecruitingTermLLMCall(None, model, None, None, None, "disabled")
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return RecruitingTermLLMCall(None, model, None, None, None, "missing_api_key")

    system_prompt = build_recruiting_term_system_prompt(
        json.dumps(RecruitingTermLLMResult.model_json_schema(), separators=(",", ":"))
    )
    content = ""
    try:
        result = complete_json(
            provider="DeepSeek",
            model_name=model,
            api_key=api_key,
            system_prompt=system_prompt,
            user_prompt=context,
            response_format={"type": "json_object"},
            timeout_seconds=float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "30")),
            max_retries=0,
        )
        parsed = RecruitingTermLLMResult.model_validate(result.data)
        prompt_tokens = result.usage.get("prompt_tokens")
        completion_tokens = result.usage.get("completion_tokens")
    except (ValidationError, ValueError, IndexError) as error:
        logger.warning("DeepSeek returned invalid recruiting term JSON (%s)", type(error).__name__)
        raw = {"raw_content": content[:2000]} if content else None
        return RecruitingTermLLMCall(
            None, model, raw, None, None, type(error).__name__
        )
    except LLMError as error:
        logger.warning("DeepSeek recruiting term request failed (%s)", type(error).__name__)
        return RecruitingTermLLMCall(None, model, None, None, None, type(error).__name__)

    response_json = parsed.model_dump(mode="json")
    extraction = None
    if parsed.found and parsed.confidence >= 0.7 and parsed.evidence:
        if _normalize(parsed.evidence) not in _normalize(context):
            return RecruitingTermLLMCall(
                None,
                model,
                response_json,
                prompt_tokens,
                completion_tokens,
                "invalid_evidence",
            )
        if _has_conflicting_month_seasons(parsed.evidence):
            return RecruitingTermLLMCall(
                None,
                model,
                response_json,
                prompt_tokens,
                completion_tokens,
            )
        extraction = RecruitingTerm(
            season=parsed.season,
            year=parsed.year,
            source="llm_description",
            evidence=parsed.evidence,
            confidence=parsed.confidence,
        )
    return RecruitingTermLLMCall(
        extraction,
        model,
        response_json,
        prompt_tokens,
        completion_tokens,
    )


def _normalize(value: str) -> str:
    normalized = re.sub(r"[*_`#]", "", value.casefold())
    normalized = re.sub(r"\s*:\s*", ": ", normalized)
    return " ".join(normalized.split())


def _has_conflicting_month_seasons(value: str) -> bool:
    month_seasons = {
        "january": "winter", "february": "winter", "december": "winter",
        "march": "spring", "april": "spring",
        "may": "summer", "june": "summer", "july": "summer", "august": "summer",
        "september": "fall", "october": "fall", "november": "fall",
    }
    months = re.findall(
        r"\b(?:january|february|march|april|may|june|july|august|"
        r"september|october|november|december)\b",
        value.casefold(),
    )
    return len({month_seasons[month] for month in months}) > 1
