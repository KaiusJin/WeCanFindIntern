"""Infrastructure-backed salary extraction used by ingestion workflows."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from wecanfindintern.domain.salary import (
    ParsedSalary,
    has_salary_signal,
    salary_signal_context,
    validated_salary,
)
from wecanfindintern.llm.gateway import LLMError, complete_json, json_response_format
from wecanfindintern.llm.prompts.salary import (
    build_salary_system_prompt,
    build_salary_user_prompt,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SalaryLLMCall:
    extraction: ParsedSalary | None
    model: str
    error_type: str | None = None


class SalaryLLMResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    found: bool
    minimum: Decimal | None
    maximum: Decimal | None
    currency: str | None
    interval: Literal["hourly", "daily", "weekly", "monthly", "yearly"] | None
    is_base_salary: bool
    confidence: float = Field(ge=0, le=1)
    evidence: str | None

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.upper()
        if not re.fullmatch(r"[A-Z]{3}", normalized):
            raise ValueError("currency must be a three-letter ISO code")
        return normalized


def extract_salary_hybrid(
    description: str | None,
    *,
    country_code: str | None = None,
    title: str | None = None,
    regex_result: ParsedSalary | None = None,
) -> ParsedSalary | None:
    if regex_result:
        return regex_result
    if not description or not has_salary_signal(description):
        return None
    return extract_salary_with_deepseek(
        description,
        country_code=country_code,
        title=title,
    )


def extract_salary_with_deepseek(
    description: str,
    *,
    country_code: str | None = None,
    title: str | None = None,
) -> ParsedSalary | None:
    return extract_salary_with_deepseek_call(
        description,
        country_code=country_code,
        title=title,
    ).extraction


def extract_salary_with_deepseek_call(
    description: str,
    *,
    country_code: str | None = None,
    title: str | None = None,
) -> SalaryLLMCall:
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    if os.getenv("DEEPSEEK_SALARY_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        return SalaryLLMCall(None, model, "disabled")
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return SalaryLLMCall(None, model, "missing_api_key")

    context = salary_signal_context(description)
    if not context:
        return SalaryLLMCall(None, model)
    default_currency = {"CA": "CAD", "US": "USD", "GB": "GBP"}.get(
        country_code or "", "USD"
    )
    system_prompt = build_salary_system_prompt(
        default_currency,
        json.dumps(SalaryLLMResult.model_json_schema(), separators=(",", ":")),
    )
    user_prompt = build_salary_user_prompt(
        context,
        title=title,
        country_code=country_code,
    )
    last_validation_error: str | None = None
    for _ in range(2):
        try:
            result = complete_json(
                provider="DeepSeek",
                model_name=model,
                api_key=api_key,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_format=json_response_format("DeepSeek"),
                timeout_seconds=float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "30")),
                max_retries=0,
            )
            parsed = SalaryLLMResult.model_validate(result.data)
        except (ValidationError, ValueError, IndexError) as error:
            logger.warning(
                "DeepSeek returned an invalid salary JSON response (%s)",
                type(error).__name__,
            )
            last_validation_error = type(error).__name__
            continue
        except LLMError as error:
            logger.warning(
                "DeepSeek salary extraction request failed (%s)",
                type(error).__name__,
            )
            return SalaryLLMCall(None, model, type(error).__name__)

        if not parsed.found or not parsed.is_base_salary or parsed.confidence < 0.7:
            return SalaryLLMCall(None, model)
        if not parsed.interval or not parsed.currency or not parsed.evidence:
            return SalaryLLMCall(None, model, "invalid_result")
        if _normalize_evidence(parsed.evidence) not in _normalize_evidence(description):
            return SalaryLLMCall(None, model, "invalid_evidence")
        extraction = validated_salary(
            interval=parsed.interval,
            minimum=parsed.minimum,
            maximum=parsed.maximum,
            currency=parsed.currency,
            source="llm_description",
        )
        if extraction is None:
            return SalaryLLMCall(None, model, "invalid_salary")
        return SalaryLLMCall(extraction, model)
    return SalaryLLMCall(None, model, last_validation_error or "invalid_result")


def _normalize_evidence(value: str) -> str:
    return " ".join(value.casefold().split())
