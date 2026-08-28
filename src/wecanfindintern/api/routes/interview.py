"""API routes for Mock Interview Coach and Answer Analysis."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile

from wecanfindintern.interview.models import (
    InterviewAnalyzeResponse,
    InterviewQuestionsRequest,
    InterviewQuestionsResponse,
)
from wecanfindintern.interview.service import (
    analyze_interview_performance,
    generate_interview_questions,
)
from wecanfindintern.interview.tts import generate_tts_audio

interview_router = APIRouter(prefix="/api/v1/interview", tags=["Mock Interview Coach"])


@interview_router.post("/questions", response_model=InterviewQuestionsResponse)
def get_questions(payload: InterviewQuestionsRequest):
    """Generate 3 tailored interview questions for the job description."""
    return generate_interview_questions(
        job_description=payload.job_description,
        provider=payload.provider,
        model_name=payload.model_name,
        api_key=payload.api_key,
    )


@interview_router.post("/tts")
def stream_tts(text: str = Form(...)):
    """Generate TTS audio stream for an interview question."""
    audio_bytes = generate_tts_audio(text)
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty text or failed TTS generation.")
    return Response(content=audio_bytes, media_type="audio/mpeg")


@interview_router.post("/analyze", response_model=InterviewAnalyzeResponse)
async def analyze_answer(
    job_description: str = Form(...),
    question_context: str = Form(...),
    answer_text: str = Form(""),
    provider: str = Form("Gemini"),
    model_name: str | None = Form(None),
    api_key: str | None = Form(None),
    video_file: Annotated[UploadFile | None, File()] = None,
):
    """Analyze mock interview answer with text transcript or recorded video."""
    video_bytes = None
    if video_file:
        video_bytes = await video_file.read()

    return analyze_interview_performance(
        job_description=job_description,
        question_context=question_context,
        answer_text=answer_text,
        video_bytes=video_bytes,
        provider=provider,
        model_name=model_name,
        api_key=api_key,
    )
