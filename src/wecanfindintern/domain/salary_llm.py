"""DeepSeek JSON-mode fallback for ambiguous salary descriptions."""

from __future__ import annotations

import json
import logging
import os
import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from wecanfindintern.domain.salary import (
    ParsedSalary,
    has_salary_signal,
    salary_signal_context,
    validated_salary,
)


logger = logging.getLogger(__name__)


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
    if os.getenv("DEEPSEEK_SALARY_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        return None
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return None

    try:
        from openai import OpenAI
    except ImportError:
        return None

    context = salary_signal_context(description)
    if not context:
        return None
    schema = SalaryLLMResult.model_json_schema()
    default_currency = {"CA": "CAD", "US": "USD", "GB": "GBP"}.get(
        country_code or "", "USD"
    )
    system_prompt = f"""
You extract base salary from job descriptions and return one JSON object only.
The JSON must conform exactly to this schema:
{json.dumps(schema, separators=(",", ":"))}

Rules:
- Never estimate market salary and never invent missing values.
- Extract base salary only. Ignore bonus, commission, equity, insurance, tuition,
  reimbursement, company revenue, benefits, and percentages.
- Treat "Salaried" or "Pay Type: Salaried" as yearly.
- If a bare $ symbol is used, default to {default_currency} for this job.
- Use a three-letter ISO currency code.
- Evidence must be an exact short substring copied from the supplied text.
- If no defensible salary exists, set found=false and all amount/currency/interval/evidence
  fields to null, is_base_salary=false, and confidence to 1.

Example JSON:
{{"found":true,"minimum":61600,"maximum":113900,"currency":"CAD",
"interval":"yearly","is_base_salary":true,"confidence":0.98,
"evidence":"$61,600.00 - $113,900.00"}}
""".strip()
    user_prompt = (
        f"Job title: {title or 'unknown'}\n"
        f"Country code: {country_code or 'unknown'}\n"
        f"Job description salary context:\n{context}"
    )
    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com"),
        timeout=float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "30")),
    )
    for _ in range(2):
        try:
            response = client.chat.completions.create(
                model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                max_tokens=500,
                temperature=0,
            )
            content = response.choices[0].message.content
            if not content:
                continue
            result = SalaryLLMResult.model_validate_json(content)
        except (ValidationError, ValueError, IndexError) as error:
            logger.warning(
                "DeepSeek returned an invalid salary JSON response (%s)",
                type(error).__name__,
            )
            continue
        except Exception as error:
            logger.warning(
                "DeepSeek salary extraction request failed (%s)",
                type(error).__name__,
            )
            return None

        if not result.found or not result.is_base_salary or result.confidence < 0.7:
            return None
        if not result.interval or not result.currency or not result.evidence:
            return None
        if _normalize_evidence(result.evidence) not in _normalize_evidence(description):
            return None
        return validated_salary(
            interval=result.interval,
            minimum=result.minimum,
            maximum=result.maximum,
            currency=result.currency,
            source="llm_description",
        )
    return None


def _normalize_evidence(value: str) -> str:
    return " ".join(value.casefold().split())
