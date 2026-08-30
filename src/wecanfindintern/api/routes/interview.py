"""API routes for Mock Interview Coach: practice sessions, TTS, and analysis."""

from __future__ import annotations

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile

from wecanfindintern.interview.models import (
    InterviewAnalyzeResponse,
    InterviewQuestionsResponse,
    InterviewSessionCreateRequest,
    InterviewSessionDetail,
    InterviewSessionSummary,
)
from wecanfindintern.interview.repository import InterviewRepository
from wecanfindintern.interview.service import (
    analyze_interview_performance,
    generate_interview_questions,
)
from wecanfindintern.interview.tts import TTSError, generate_tts_audio

interview_router = APIRouter(prefix="/api/v1/interview", tags=["Mock Interview Coach"])


def _repo(request: Request) -> InterviewRepository:
    return InterviewRepository(request.app.state.database.pool)


@interview_router.post("/sessions", status_code=201)
async def create_interview_session(payload: InterviewSessionCreateRequest, request: Request):
    """Generate a question set and persist it as a practice session."""
    questions_response = await asyncio.to_thread(
        generate_interview_questions,
        job_description=payload.job_description,
        resume_text=payload.resume_text,
        provider=payload.provider,
        model_name=payload.model_name,
        api_key=payload.api_key,
        api_base=payload.api_base,
    )
    if not questions_response.ok:
        status_code = 422 if "required" in (questions_response.error or "") else 502
        raise HTTPException(status_code=status_code, detail=questions_response.error)
    session = await _repo(request).create_session(
        job_description=payload.job_description,
        provider=payload.provider,
        model_name=payload.model_name or "",
        questions=[
            question.model_dump(mode="json")
            for question in questions_response.questions
        ],
    )
    return {
        "ok": True,
        "session_id": str(session["id"]),
        "questions": questions_response.questions,
        "usage": questions_response.usage,
    }


@interview_router.get("/sessions", response_model=list[InterviewSessionSummary])
async def list_interview_sessions(request: Request) -> list[InterviewSessionSummary]:
    rows = await _repo(request).list_sessions()
    return [
        InterviewSessionSummary(
            id=row["id"],
            provider=row["provider"],
            model_name=row["model_name"],
            question_count=int(row["question_count"]),
            answer_count=int(row["answer_count"]),
            avg_score=int(row["avg_score"]),
            created_at=row["created_at"],
            last_practiced_at=row["last_practiced_at"],
        )
        for row in rows
    ]


@interview_router.get("/sessions/{session_id}", response_model=InterviewSessionDetail)
async def get_interview_session(session_id: UUID, request: Request) -> InterviewSessionDetail:
    session = await _repo(request).get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Interview session not found")
    return InterviewSessionDetail(
        id=session["id"],
        job_description=session["job_description"],
        provider=session["provider"],
        model_name=session["model_name"],
        questions=session["questions"] or [],
        answers=session["answers"] or [],
        created_at=session["created_at"],
        updated_at=session["updated_at"],
    )


@interview_router.delete("/sessions/{session_id}")
async def delete_interview_session(session_id: UUID, request: Request) -> dict[str, bool]:
    deleted = await _repo(request).delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Interview session not found")
    return {"deleted": True}


@interview_router.get("/trend")
async def interview_trend(request: Request) -> dict:
    return await _repo(request).practice_trend()


@interview_router.post("/questions", response_model=InterviewQuestionsResponse)
def get_questions(payload: InterviewSessionCreateRequest):
    """Generate 7 tailored technical interview questions without a session."""
    return generate_interview_questions(
        job_description=payload.job_description,
        resume_text=payload.resume_text,
        provider=payload.provider,
        model_name=payload.model_name,
        api_key=payload.api_key,
        api_base=payload.api_base,
    )


@interview_router.post("/tts")
def stream_tts(text: str = Form(...)):
    """Generate question audio with the configured TTS backend."""
    try:
        audio = generate_tts_audio(text)
    except TTSError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if not audio.data:
        raise HTTPException(status_code=400, detail="Empty text or failed TTS generation.")
    return Response(content=audio.data, media_type=audio.media_type)


@interview_router.post("/analyze", response_model=InterviewAnalyzeResponse)
async def analyze_answer(
    request: Request,
    job_description: str = Form(...),
    question_context: str = Form(...),
    answer_text: str = Form(""),
    provider: str = Form("Gemini"),
    model_name: str | None = Form(None),
    api_key: str | None = Form(None),
    api_base: str | None = Form(None),
    audio_file: Annotated[UploadFile | None, File()] = None,
    session_id: Annotated[UUID | None, Form()] = None,
    question_index: Annotated[int | None, Form()] = None,
    question_criteria: Annotated[str, Form()] = "",
):
    """Analyze a mock interview answer; persists the report when a practice
    session and question index are supplied."""
    audio_bytes = None
    audio_mime = "audio/webm"
    if audio_file:
        audio_bytes = await audio_file.read()
        if audio_file.content_type:
            audio_mime = audio_file.content_type

    # Transcription plus the LLM round-trip can take minutes; run it in a
    # worker thread so the event loop keeps serving other requests.
    response = await asyncio.to_thread(
        analyze_interview_performance,
        job_description=job_description,
        question_context=question_context,
        answer_text=answer_text,
        audio_bytes=audio_bytes,
        audio_mime=audio_mime,
        evaluation_criteria=question_criteria,
        provider=provider,
        model_name=model_name,
        api_key=api_key,
        api_base=api_base,
    )
    if response.ok and session_id is not None and question_index is not None:
        await _repo(request).upsert_answer(
            session_id=session_id,
            question_index=question_index,
            question_text=question_context,
            answer_text=answer_text,
            transcript=response.transcript,
            transcript_language=response.transcript_language,
            duration_seconds=response.answer_duration_seconds,
            score=response.score,
            summary=response.summary,
            star_feedback=response.star_feedback,
            timeline=[event.model_dump(mode="json") for event in response.timeline],
            advice=list(response.advice),
            provider=provider,
            model_name=model_name or "",
        )
    return response
