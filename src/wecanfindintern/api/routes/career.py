"""FastAPI router for Career AI features (ATS Review, Interview Coach, Cover Letter)."""

from __future__ import annotations

from typing import Literal
from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import Response

from wecanfindintern.career_ai.export import export_docx, export_latex, export_pdf
from wecanfindintern.career_ai.models import (
    AtsReviewRequest,
    AtsReviewResponse,
    CoverLetterExportRequest,
    CoverLetterRequest,
    CoverLetterResponse,
    InterviewAnalyzeResponse,
    InterviewQuestionsRequest,
    InterviewQuestionsResponse,
    UserProfile,
)
from wecanfindintern.career_ai.service import (
    analyze_interview_performance,
    extract_text_from_pdf,
    generate_ats_review,
    generate_cover_letter,
    generate_interview_questions,
    generate_tts_audio,
)

career_router = APIRouter(prefix="/api/v1/career", tags=["Career AI"])


@career_router.post("/extract-pdf")
async def extract_pdf(file: UploadFile = File(...)) -> dict[str, Any]:
    """Extract raw text from an uploaded PDF file."""
    try:
        content = await file.read()
        text = extract_text_from_pdf(content)
        return {"ok": True, "text": text}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "text": ""}


@career_router.post("/ats-review", response_model=AtsReviewResponse)
async def ats_review(payload: AtsReviewRequest) -> AtsReviewResponse:
    """Evaluate candidate resume against a target job description."""
    return generate_ats_review(
        resume_text=payload.resume_text,
        job_description=payload.job_description,
        provider=payload.provider,
        model_name=payload.model_name,
        api_key=payload.api_key,
    )


@career_router.post("/interview/questions", response_model=InterviewQuestionsResponse)
async def interview_questions(payload: InterviewQuestionsRequest) -> InterviewQuestionsResponse:
    """Generate 3-stage tailored mock interview loop."""
    return generate_interview_questions(
        job_description=payload.job_description,
        provider=payload.provider,
        model_name=payload.model_name,
        api_key=payload.api_key,
    )


@career_router.post("/interview/tts")
async def interview_tts(payload: dict[str, str]) -> Response:
    """Generate TTS MP3 audio for reading interview questions."""
    text = payload.get("text", "")
    audio_bytes = generate_tts_audio(text)
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Failed to generate TTS audio.")
    return Response(content=audio_bytes, media_type="audio/mpeg")


@career_router.post("/interview/analyze", response_model=InterviewAnalyzeResponse)
async def interview_analyze(
    job_description: str = Form(...),
    question_context: str = Form(""),
    answer_text: str = Form(""),
    provider: Literal["Gemini", "OpenAI"] = Form("Gemini"),
    model_name: str = Form(""),
    api_key: str = Form(""),
    video_file: UploadFile | None = File(None),
) -> InterviewAnalyzeResponse:
    """Analyze mock interview answer performance (text or uploaded video/audio)."""
    video_bytes = None
    if video_file:
        video_bytes = await video_file.read()

    return analyze_interview_performance(
        job_description=job_description,
        question_context=question_context,
        answer_text=answer_text,
        video_bytes=video_bytes,
        provider=provider,
        model_name=model_name or None,
        api_key=api_key or None,
    )


@career_router.post("/cover-letter/generate", response_model=CoverLetterResponse)
async def cover_letter_generate(payload: CoverLetterRequest) -> CoverLetterResponse:
    """Generate tailored cover letter."""
    return generate_cover_letter(
        resume_text=payload.resume_text,
        job_description=payload.job_description,
        user_info=payload.user_info,
        date_str=payload.date_str,
        provider=payload.provider,
        model_name=payload.model_name,
        api_key=payload.api_key,
    )


@career_router.post("/cover-letter/export")
async def cover_letter_export(payload: CoverLetterExportRequest) -> Response:
    """Export cover letter as Word (.docx), PDF (.pdf), or LaTeX (.tex)."""
    fmt = payload.format.lower()
    if fmt == "docx":
        data = export_docx(payload.body, payload.user_info)
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": 'attachment; filename="Cover_Letter.docx"'},
        )
    if fmt == "pdf":
        data = export_pdf(payload.body, payload.user_info)
        return Response(
            content=data,
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="Cover_Letter.pdf"'},
        )
    if fmt == "tex":
        tex_code = export_latex(payload.body, payload.user_info, payload.date_str)
        return Response(
            content=tex_code.encode("utf-8"),
            media_type="application/x-tex",
            headers={"Content-Disposition": 'attachment; filename="Cover_Letter.tex"'},
        )
    raise HTTPException(status_code=400, detail=f"Unsupported format: {fmt}")
