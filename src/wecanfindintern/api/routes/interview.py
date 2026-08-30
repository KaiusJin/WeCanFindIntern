"""API routes for Mock Interview Coach: practice sessions, TTS, and analysis."""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse

from wecanfindintern.interview.models import (
    InterviewAnalyzeResponse,
    InterviewQuestionItem,
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
from wecanfindintern.llm.gateway import (
    LLMError,
    extract_json_array_objects,
    parse_json,
    resolve_api_key,
    stream_text,
)
from wecanfindintern.llm.prompts.interview import build_questions_prompt

INTERVIEWER_SYSTEM_PROMPT = (
    "You are a professional technical interviewer. Output valid JSON."
)

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


@interview_router.post("/sessions/stream")
async def create_interview_session_stream(
    payload: InterviewSessionCreateRequest, request: Request
):
    """SSE variant of session creation: emits a ``question`` event the moment
    each object closes in the model's JSON array, then ``done`` with the
    persisted session id and the validated question set."""

    async def event_stream():
        async def run():
            if not payload.job_description.strip():
                yield {"type": "error", "status": 422, "detail": "Job description cannot be empty."}
                return
            if not payload.resume_text.strip():
                yield {
                    "type": "error",
                    "status": 422,
                    "detail": "Candidate context is required: upload a resume or use your Profile.",
                }
                return
            try:
                resolved_key = resolve_api_key(payload.provider, payload.api_key)
            except LLMError as exc:
                yield {"type": "error", "status": 422, "detail": str(exc)}
                return

            queue: asyncio.Queue[dict | None] = asyncio.Queue()
            loop = asyncio.get_running_loop()
            buffer = ""
            questions: list[dict] = []
            errors: list[Exception] = []

            def produce() -> None:
                nonlocal buffer
                try:
                    for chunk in stream_text(
                        provider=payload.provider,
                        model_name=payload.model_name or "",
                        api_key=resolved_key,
                        api_base=payload.api_base,
                        system_prompt=INTERVIEWER_SYSTEM_PROMPT,
                        user_prompt=build_questions_prompt(
                            payload.job_description, payload.resume_text
                        ),
                    ):
                        buffer += chunk
                        objects, buffer = extract_json_array_objects(buffer)
                        for item in objects:
                            try:
                                question = InterviewQuestionItem.model_validate(item)
                            except (ValueError, TypeError):
                                continue
                            questions.append(question.model_dump(mode="json"))
                            loop.call_soon_threadsafe(
                                queue.put_nowait,
                                {"type": "question", "question": question.model_dump(mode="json")},
                            )
                except Exception as error:
                    errors.append(error)
                finally:
                    loop.call_soon_threadsafe(queue.put_nowait, None)

            threading.Thread(target=produce, daemon=True).start()
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            if errors and not questions:
                yield {
                    "type": "error",
                    "status": 502,
                    "detail": f"{payload.provider} questions error: {errors[0]}",
                }
                return

            if not questions:
                # Envelope fallback: the model wrapped the array in an object
                # instead of streaming a bare array.
                try:
                    data = parse_json(buffer)
                    if isinstance(data, dict) and "questions" in data:
                        data = data["questions"]
                    questions = [
                        InterviewQuestionItem.model_validate(item).model_dump(mode="json")
                        for item in data
                    ]
                except (LLMError, ValueError, TypeError, KeyError):
                    pass
            if not questions:
                yield {
                    "type": "error",
                    "status": 502,
                    "detail": "Model returned no parseable questions.",
                }
                return

            session = await _repo(request).create_session(
                job_description=payload.job_description,
                provider=payload.provider,
                model_name=payload.model_name or "",
                questions=questions,
            )
            yield {
                "type": "done",
                "session_id": str(session["id"]),
                "questions": questions,
            }

        async for event in run():
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


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
