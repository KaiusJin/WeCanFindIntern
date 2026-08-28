"""ATS resume review and keyword evaluation service."""

from __future__ import annotations

import json
import os
from typing import Any

from wecanfindintern.ats.models import AtsReviewResponse
from wecanfindintern.llm.client import (
    call_gemini,
    call_openai_compatible,
    clean_json_text,
    resolve_api_key,
)


def match_level(score: int) -> str:
    if score >= 80:
        return "高匹配 (High Match)"
    if score >= 55:
        return "中匹配 (Medium Match)"
    return "基础匹配 (Low Match)"


def generate_ats_review(
    resume_text: str,
    job_description: str,
    provider: str = "Gemini",
    model_name: str | None = None,
    api_key: str | None = None,
) -> AtsReviewResponse:
    """Evaluate candidate resume against a target job description."""
    if not resume_text.strip():
        return AtsReviewResponse(ok=False, error="Resume text cannot be empty.")
    if not job_description.strip():
        return AtsReviewResponse(ok=False, error="Job description cannot be empty.")
    if not model_name or not model_name.strip():
        return AtsReviewResponse(ok=False, error="No AI model selected. Please select a model in Settings.")

    try:
        resolved_key = resolve_api_key(provider, api_key)
    except ValueError as exc:
        return AtsReviewResponse(ok=False, error=str(exc))

    prompt = f"""
You are an expert Applicant Tracking System (ATS) and Technical Recruiter.
Evaluate how well the following candidate resume matches the target job description.

Target Job Description:
{job_description}

Candidate Resume Context:
{resume_text}

Evaluation Guidelines:
1. Score from 0 to 100 based strictly on skill relevance, keyword alignment, experience scope, and role fit.
2. List key matching strengths (technologies, methodologies, relevant scope).
3. List critical missing keywords, experience gaps, or qualification mismatches.
4. Give 3-5 concrete, actionable bullet improvement suggestions for the candidate.

Return a VALID JSON object ONLY with this exact schema:
{{
  "score": <int 0-100>,
  "summary": "<2-3 sentence overview of candidate match>",
  "strengths": ["<strength 1>", "<strength 2>", ...],
  "gaps": ["<gap/missing keyword 1>", "<gap/missing keyword 2>", ...],
  "suggestions": ["<actionable advice 1>", "<actionable advice 2>", ...]
}}
"""

    if provider in ("DeepSeek", "OpenAI"):
        try:
            base_url = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com") if provider == "DeepSeek" else None
            raw_content, total_tokens = call_openai_compatible(
                api_key=resolved_key,
                model_name=model_name,
                messages=[
                    {"role": "system", "content": "You are a professional ATS system. Output strictly valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                base_url=base_url,
                response_format={"type": "json_object"},
            )
            raw = clean_json_text(raw_content)
            data = json.loads(raw)
            score = int(data.get("score", 0))
            return AtsReviewResponse(
                ok=True,
                score=score,
                level=match_level(score),
                summary=data.get("summary", ""),
                strengths=data.get("strengths", []),
                gaps=data.get("gaps", []),
                suggestions=data.get("suggestions", []),
                usage={"total_tokens": total_tokens},
            )
        except Exception as exc:
            return AtsReviewResponse(ok=False, error=f"{provider} ATS error: {exc}")

    # Gemini
    try:
        resp_text = call_gemini(resolved_key, prompt, model_name)
        raw_json = clean_json_text(resp_text)
        data = json.loads(raw_json)
        score = int(data.get("score", 0))
        return AtsReviewResponse(
            ok=True,
            score=score,
            level=match_level(score),
            summary=data.get("summary", ""),
            strengths=data.get("strengths", []),
            gaps=data.get("gaps", []),
            suggestions=data.get("suggestions", []),
        )
    except Exception as exc:
        return AtsReviewResponse(ok=False, error=f"Gemini ATS error: {exc}")
