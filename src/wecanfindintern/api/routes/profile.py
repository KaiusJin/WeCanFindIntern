"""API routes for profile editing and safe resume imports."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile

from wecanfindintern.api.dependencies import get_profile_repository
from wecanfindintern.application.profile_context import profile_resume_text
from wecanfindintern.profile.models import (
    ProfilePayload,
    ResumeDocumentSummary,
    ResumeImportResult,
    UserProfile,
)
from wecanfindintern.profile.parser import PARSER_VERSION, parse_resume_text
from wecanfindintern.profile.repository import ProfileRepository
from wecanfindintern.profile.security import MAX_PDF_BYTES, validate_and_extract_resume

profile_router = APIRouter(prefix="/api/v1/profile", tags=["Profile"])


ProfileRepoDep = Annotated[ProfileRepository, Depends(get_profile_repository)]


@profile_router.get("", response_model=UserProfile)
async def get_profile(repo: ProfileRepoDep) -> UserProfile:
    return await repo.get_profile()


@profile_router.put("", response_model=UserProfile)
async def update_profile(payload: ProfilePayload, repo: ProfileRepoDep) -> UserProfile:
    return await repo.save_profile(payload)


@profile_router.get("/export", response_model=UserProfile)
async def export_profile(repo: ProfileRepoDep) -> UserProfile:
    return await repo.get_profile()


@profile_router.get("/context")
async def get_profile_context(repo: ProfileRepoDep) -> dict:
    profile = await repo.get_profile()
    return {
        "profile": profile.model_dump(mode="json"),
        "resume_text": profile_resume_text(profile),
    }


@profile_router.post("/resumes", response_model=ResumeImportResult, status_code=201)
async def upload_resume(
    repo: ProfileRepoDep, file: Annotated[UploadFile, File()]
) -> ResumeImportResult:
    content = await file.read(MAX_PDF_BYTES + 1)
    try:
        validated = validate_and_extract_resume(file.filename, file.content_type, content)
        draft = parse_resume_text(validated.extracted_text)
        resume, import_id = await repo.create_resume_import(
            filename=file.filename or "resume",
            source_type=validated.source_type,
            media_type=validated.media_type,
            content=content,
            extracted_text=validated.extracted_text,
            parser_version=PARSER_VERSION,
            payload=draft,
            warnings=list(validated.warnings),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return ResumeImportResult(
        import_id=import_id,
        resume=resume,
        draft=draft,
        extracted_text=validated.extracted_text,
        warnings=list(validated.warnings),
    )


@profile_router.get("/resumes", response_model=list[ResumeDocumentSummary])
async def list_resumes(repo: ProfileRepoDep) -> list[ResumeDocumentSummary]:
    return await repo.list_resumes()


@profile_router.delete("/resumes/{resume_id}")
async def delete_resume(resume_id: UUID, repo: ProfileRepoDep) -> dict[str, bool]:
    if not await repo.delete_resume(resume_id):
        raise HTTPException(status_code=404, detail="Resume not found")
    return {"ok": True}


@profile_router.post("/imports/{import_id}/confirm", response_model=UserProfile)
async def confirm_import(
    import_id: UUID,
    repo: ProfileRepoDep,
    payload: Annotated[ProfilePayload | None, Body()] = None,
) -> UserProfile:
    profile = await repo.confirm_import(import_id, payload)
    if not profile:
        raise HTTPException(status_code=404, detail="Resume import draft not found")
    return profile


@profile_router.put("/imports/{import_id}", response_model=ProfilePayload)
async def autosave_import_draft(
    import_id: UUID,
    payload: ProfilePayload,
    repo: ProfileRepoDep,
) -> ProfilePayload:
    saved = await repo.update_import_draft(import_id, payload)
    if saved is None:
        raise HTTPException(status_code=404, detail="Resume import draft not found")
    return saved
