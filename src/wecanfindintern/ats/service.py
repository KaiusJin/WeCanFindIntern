"""ATS resume review and keyword evaluation service."""

from __future__ import annotations

from wecanfindintern.ats.models import AtsReviewResponse
from wecanfindintern.llm.gateway import LLMError, complete_json, resolve_api_key
from wecanfindintern.llm.prompts.ats import ATS_SYSTEM_PROMPT, build_ats_prompt


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
    api_base: str | None = None,
) -> AtsReviewResponse:
    """Evaluate candidate resume against a target job description."""
    if not resume_text.strip():
        return AtsReviewResponse(ok=False, error="Resume text cannot be empty.")
    if not job_description.strip():
        return AtsReviewResponse(ok=False, error="Job description cannot be empty.")

    try:
        resolved_key = resolve_api_key(provider, api_key)
    except LLMError as exc:
        return AtsReviewResponse(ok=False, error=str(exc))

    try:
        result = complete_json(
            provider=provider,
            model_name=model_name,
            api_key=resolved_key,
            api_base=api_base,
            system_prompt=ATS_SYSTEM_PROMPT,
            user_prompt=build_ats_prompt(resume_text, job_description),
            response_format=(
                {"type": "json_object"}
                if provider in ("OpenAI", "DeepSeek", "GLM", "Qwen", "Ollama")
                else None
            ),
        )
        data = result.data
        score = int(data.get("score", 0))
        return AtsReviewResponse(
            ok=True,
            score=score,
            level=match_level(score),
            summary=data.get("summary", ""),
            strengths=data.get("strengths", []),
            gaps=data.get("gaps", []),
            suggestions=data.get("suggestions", []),
            usage=result.usage,
        )
    except (LLMError, ValueError, TypeError, KeyError) as exc:
        return AtsReviewResponse(ok=False, error=f"{provider} ATS error: {exc}")
