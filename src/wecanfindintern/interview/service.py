"""Mock interview question generation and answer evaluation service.

Answers are analyzed as text for every provider. A recorded audio answer is
transcribed locally first (``interview/stt.py``), so the whole feature is
provider-agnostic — no multimodal lock-in.
"""

from __future__ import annotations

from typing import Any

from wecanfindintern.interview.models import (
    InterviewAnalyzeResponse,
    InterviewQuestionItem,
    InterviewQuestionsResponse,
)
from wecanfindintern.interview.stt import STTError, transcribe_audio
from wecanfindintern.llm.gateway import (
    LLMError,
    complete_json,
    json_response_format,
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
    api_base: str | None = None,
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
            api_base=api_base,
            system_prompt="You are a professional technical interviewer. Output valid JSON.",
            user_prompt=build_questions_prompt(job_description),
            response_format=json_response_format(provider),
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
    audio_bytes: bytes | None = None,
    audio_mime: str = "audio/webm",
    provider: str = "Gemini",
    model_name: str | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
) -> InterviewAnalyzeResponse:
    """Analyze mock interview answer performance (typed text or audio answer)."""

    try:
        resolved_key = resolve_api_key(provider, api_key)
    except LLMError as exc:
        return InterviewAnalyzeResponse(ok=False, error=str(exc))

    transcript_text = ""
    transcript_language = ""
    answer_duration = 0.0
    if audio_bytes:
        try:
            transcript = transcribe_audio(audio_bytes, mime=audio_mime)
        except STTError as exc:
            return InterviewAnalyzeResponse(ok=False, error=str(exc))
        transcript_text = transcript.text
        transcript_language = transcript.language
        answer_duration = transcript.duration_seconds
        if not answer_text.strip():
            answer_text = transcript_text

    if not answer_text.strip():
        return InterviewAnalyzeResponse(
            ok=False,
            error="Provide a typed answer or record an audio answer to analyze.",
        )

    prompt_text = build_analysis_prompt(job_description, question_context, answer_text)

    try:
        result = complete_json(
            provider=provider,
            model_name=model_name,
            api_key=resolved_key,
            api_base=api_base,
            system_prompt="You are a professional interview coach. Output valid JSON.",
            user_prompt=prompt_text,
            response_format=json_response_format(provider),
        )
        return _analysis_from_data(
            result.data,
            usage=result.usage,
            transcript=transcript_text,
            transcript_language=transcript_language,
            answer_duration_seconds=answer_duration,
        )
    except (LLMError, ValueError, TypeError, KeyError) as exc:
        return InterviewAnalyzeResponse(ok=False, error=f"{provider} answer analysis error: {exc}")


def _analysis_from_data(
    data: dict[str, Any],
    *,
    usage: dict[str, Any] | None = None,
    transcript: str = "",
    transcript_language: str = "",
    answer_duration_seconds: float = 0.0,
) -> InterviewAnalyzeResponse:
    """Build the public analysis response from an LLM JSON payload."""

    return InterviewAnalyzeResponse(
        ok=True,
        score=int(data.get("score", 75)),
        summary=data.get("summary", ""),
        star_feedback=data.get("star_feedback", ""),
        timeline=data.get("timeline", []),
        advice=data.get("advice", []),
        transcript=transcript,
        transcript_language=transcript_language,
        answer_duration_seconds=answer_duration_seconds,
        usage=usage or {},
    )
