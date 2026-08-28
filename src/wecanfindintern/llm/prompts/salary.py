"""Prompts for DeepSeek salary extraction fallback."""

from __future__ import annotations


def build_salary_system_prompt(default_currency: str, schema_json: str) -> str:
    return f"""
You extract base salary from job descriptions and return one JSON object only.
The JSON must conform exactly to this schema:
{schema_json}

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


def build_salary_user_prompt(
    context: str,
    *,
    title: str | None,
    country_code: str | None,
) -> str:
    return (
        f"Job title: {title or 'unknown'}\n"
        f"Country code: {country_code or 'unknown'}\n"
        f"Job description salary context:\n{context}"
    )
