"""Cover letter generation service."""

from __future__ import annotations

import json
import os
from typing import Any

from wecanfindintern.cover_letter.models import CoverLetterResponse, UserProfile
from wecanfindintern.llm.client import (
    call_gemini,
    call_openai_compatible,
    clean_json_text,
    resolve_api_key,
)


def generate_cover_letter(
    resume_text: str,
    job_description: str,
    user_info: UserProfile,
    date_str: str = "[Date]",
    provider: str = "Gemini",
    model_name: str | None = None,
    api_key: str | None = None,
) -> CoverLetterResponse:
    """Generate tailored cover letter."""
    if not resume_text.strip():
        return CoverLetterResponse(ok=False, error="Resume text cannot be empty.")
    if not job_description.strip():
        return CoverLetterResponse(ok=False, error="Job description cannot be empty.")
    if not model_name or not model_name.strip():
        return CoverLetterResponse(ok=False, error="No AI model selected. Please select a model in Settings.")

    try:
        resolved_key = resolve_api_key(provider, api_key)
    except ValueError as exc:
        return CoverLetterResponse(ok=False, error=str(exc))

    prompt = f"""
You are a Senior Copywriter and Executive Career Coach.
Task: Write a concise, hyper-personalized, and compelling cover letter.

Target Job Description:
{job_description}

Candidate Resume Context:
{resume_text}

Candidate Profile Details:
- Name: {user_info.full_name or 'Applicant'}
- Email: {user_info.email or ''}
- Phone: {user_info.phone or ''}
- LinkedIn: {user_info.linkedin or ''}
- Address: {user_info.address or ''}

Date: {date_str}

STYLE & CONTENT RULES:
1. 2 to 3 focused paragraphs. Target 200–280 words.
2. Weave the candidate's concrete projects and quantified achievements into the key responsibilities of the role.
3. Use active voice, professional tone, and avoid buzzword clichés.
4. Output strictly formatted letter text starting with the date and recipient block.

Return a VALID JSON object ONLY:
{{
  "hr_info": {{
    "company": "<Company Name>",
    "manager": "<Hiring Manager Name or 'Hiring Team'>",
    "address": "<Company Location or 'Headquarters'>"
  }},
  "cover_letter": "<Full formatted text of the cover letter with paragraphs>"
}}
"""

    if provider in ("DeepSeek", "OpenAI"):
        try:
            base_url = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com") if provider == "DeepSeek" else None
            raw_content, total_tokens = call_openai_compatible(
                api_key=resolved_key,
                model_name=model_name,
                messages=[
                    {"role": "system", "content": "You are a professional cover letter writer. Output valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                base_url=base_url,
                response_format={"type": "json_object"},
            )
            raw = clean_json_text(raw_content)
            data = json.loads(raw)
            return CoverLetterResponse(
                ok=True,
                text=data.get("cover_letter", ""),
                hr_info=data.get("hr_info", {}),
                usage={"total_tokens": total_tokens},
            )
        except Exception as exc:
            return CoverLetterResponse(ok=False, error=f"{provider} cover letter error: {exc}")

    # Gemini
    try:
        resp_text = call_gemini(resolved_key, prompt, model_name)
        raw_json = clean_json_text(resp_text)
        data = json.loads(raw_json)
        return CoverLetterResponse(
            ok=True,
            text=data.get("cover_letter", ""),
            hr_info=data.get("hr_info", {}),
        )
    except Exception as exc:
        return CoverLetterResponse(ok=False, error=f"Gemini cover letter error: {exc}")
