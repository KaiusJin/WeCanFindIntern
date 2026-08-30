"""API routes for Cover Letter generation and export."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from wecanfindintern.cover_letter.export import export_docx, export_pdf
from wecanfindintern.cover_letter.models import (
    CoverLetterExportRequest,
    CoverLetterRequest,
    CoverLetterResponse,
)
from wecanfindintern.cover_letter.service import generate_cover_letter

cover_letter_router = APIRouter(prefix="/api/v1/cover-letter", tags=["Cover Letter Generator"])


@cover_letter_router.post("/generate", response_model=CoverLetterResponse)
def run_cover_letter_generation(payload: CoverLetterRequest):
    """Generate hyper-personalized cover letter."""
    return generate_cover_letter(
        resume_text=payload.resume_text,
        job_description=payload.job_description,
        user_info=payload.user_info,
        job_title=payload.job_title,
        company_name=payload.company_name,
        company_location=payload.company_location,
        hiring_manager=payload.hiring_manager,
        company_information=payload.company_information,
        date_str=payload.date_str,
        provider=payload.provider,
        model_name=payload.model_name,
        api_key=payload.api_key,
        api_base=payload.api_base,
    )


@cover_letter_router.post("/generate/stream")
async def run_cover_letter_generation_stream(payload: CoverLetterRequest, request: Request):
    """SSE variant: pushes pipeline stage events (writer/reviewer/revision)
    while the generation runs, then the full result payload."""

    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_stage(stage: str, **detail: object) -> None:
        loop.call_soon_threadsafe(
            queue.put_nowait, {"type": "stage", "stage": stage, **detail}
        )

    async def generate() -> CoverLetterResponse:
        return await asyncio.to_thread(
            generate_cover_letter,
            resume_text=payload.resume_text,
            job_description=payload.job_description,
            user_info=payload.user_info,
            job_title=payload.job_title,
            company_name=payload.company_name,
            company_location=payload.company_location,
            hiring_manager=payload.hiring_manager,
            company_information=payload.company_information,
            date_str=payload.date_str,
            provider=payload.provider,
            model_name=payload.model_name,
            api_key=payload.api_key,
            api_base=payload.api_base,
            on_stage=on_stage,
        )

    async def event_stream():
        generate_task = asyncio.create_task(generate())
        while not generate_task.done():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        result = await generate_task
        # Drain any stage events queued after the task finished.
        while not queue.empty():
            event = queue.get_nowait()
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        yield (
            "data: "
            + json.dumps(
                {"type": "done", "result": result.model_dump(mode="json")},
                ensure_ascii=False,
            )
            + "\n\n"
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@cover_letter_router.post("/export")
def cover_letter_export(payload: CoverLetterExportRequest):
    """Export formatted cover letter as docx or pdf."""
    fmt = payload.format.lower()
    if fmt == "docx":
        docx_bytes = export_docx(payload.body, payload.user_info)
        return Response(
            content=docx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": 'attachment; filename="Cover_Letter.docx"'},
        )
    elif fmt == "pdf":
        pdf_bytes = export_pdf(payload.body, payload.user_info)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="Cover_Letter.pdf"'},
        )
    else:
        raise HTTPException(status_code=400, detail="Unsupported export format.")
