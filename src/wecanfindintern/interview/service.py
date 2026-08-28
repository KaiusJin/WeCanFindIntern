"""Mock interview question generation and answer evaluation service."""

from __future__ import annotations

from typing import Any

from wecanfindintern.interview.models import (
    InterviewAnalyzeResponse,
    InterviewQuestionItem,
    InterviewQuestionsResponse,
)
from wecanfindintern.llm.gateway import (
    LLMError,
    complete_json,
    parse_json,
    resolve_api_key,
)
from wecanfindintern.llm.prompts.interview import (
    build_analysis_prompt,
    build_questions_prompt,
)


def generate_interview_questions(
    job_description: str,
    provider: str = "Gemini",
    model_name: str | None = None,
    api_key: str | None = None,
) -> InterviewQuestionsResponse:
    """Generate 3-stage tailored mock interview loop."""
    if not job_description.strip():
        return InterviewQuestionsResponse(ok=False, error="Job description cannot be empty.")

    try:
        resolved_key = resolve_api_key(provider, api_key)
    except LLMError as exc:
        return InterviewQuestionsResponse(ok=False, error=str(exc))

    try:
        result = complete_json(
            provider=provider,
            model_name=model_name,
            api_key=resolved_key,
            system_prompt="You are a professional technical interviewer. Output valid JSON.",
            user_prompt=build_questions_prompt(job_description),
        )
        data = result.data
        if isinstance(data, dict) and "questions" in data:
            data = data["questions"]
        questions = [InterviewQuestionItem.model_validate(q) for q in data]
        return InterviewQuestionsResponse(ok=True, questions=questions, usage=result.usage)
    except (LLMError, ValueError, TypeError, KeyError) as exc:
        return InterviewQuestionsResponse(ok=False, error=f"{provider} questions error: {exc}")


def analyze_interview_performance(
    job_description: str,
    question_context: str,
    answer_text: str = "",
    video_bytes: bytes | None = None,
    provider: str = "Gemini",
    model_name: str | None = None,
    api_key: str | None = None,
) -> InterviewAnalyzeResponse:
    """Analyze mock interview answer performance."""

    try:
        resolved_key = resolve_api_key(provider, api_key)
    except LLMError as exc:
        return InterviewAnalyzeResponse(ok=False, error=str(exc))

    prompt_text = build_analysis_prompt(job_description, question_context, answer_text)

    if video_bytes and provider == "Gemini":
        try:
            import google.generativeai as genai

            clean_key = resolve_api_key("Gemini", api_key)
            genai.configure(api_key=clean_key, transport="rest")
            target_model = model_name.strip().replace("models/", "")
            model = genai.GenerativeModel(target_model)
            contents = [
                {"mime_type": "video/webm", "data": video_bytes},
                prompt_text,
            ]
            resp = model.generate_content(contents, request_options={"timeout": 180.0})
            data = parse_json(resp.text)
            return _analysis_from_data(data)
        except (LLMError, ValueError, TypeError, KeyError) as exc:
            return InterviewAnalyzeResponse(ok=False, error=f"Gemini video analysis error: {exc}")

    try:
        result = complete_json(
            provider=provider,
            model_name=model_name,
            api_key=resolved_key,
            system_prompt="You are a professional interview coach. Output valid JSON.",
            user_prompt=prompt_text,
            response_format=(
                {"type": "json_object"} if provider in ("OpenAI", "DeepSeek") else None
            ),
        )
        return _analysis_from_data(result.data, usage=result.usage)
    except (LLMError, ValueError, TypeError, KeyError) as exc:
        return InterviewAnalyzeResponse(ok=False, error=f"{provider} answer analysis error: {exc}")


def _analysis_from_data(
    data: dict[str, Any],
    *,
    usage: dict[str, Any] | None = None,
) -> InterviewAnalyzeResponse:
    """Build the public analysis response from an LLM JSON payload."""

    return InterviewAnalyzeResponse(
        ok=True,
        score=int(data.get("score", 75)),
        summary=data.get("summary", ""),
        star_feedback=data.get("star_feedback", ""),
        timeline=data.get("timeline", []),
        advice=data.get("advice", []),
        usage=usage or {},
    )
