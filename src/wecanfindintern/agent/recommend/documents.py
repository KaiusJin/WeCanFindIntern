"""Build privacy-bounded profile queries and versioned job RAG documents."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from wecanfindintern.profile.models import UserProfile

DOCUMENT_VERSION = "recommend-document.v2"
CHUNK_WORDS = 360
CHUNK_OVERLAP_WORDS = 50


@dataclass(frozen=True, slots=True)
class RecommendationDocument:
    source_job_id: str
    public_job_id: str | None
    content_hash: str
    title: str
    role_family: str | None
    normalized_skills: list[str]
    required_skills: list[str]
    preferred_skills: list[str]
    document_text: str
    metadata: dict[str, Any]
    chunks: list[str]


def build_profile_query(profile: UserProfile, preferences: dict[str, str]) -> str:
    """Build matching text without names, contact details, or profile URLs."""

    sections: list[str] = []
    skills = sorted(
        {
            entry.name.strip()
            for entry in profile.skills
            if entry.name and entry.name.strip()
        }
        | {
            skill.strip()
            for project in profile.projects
            for skill in project.skills
            if skill.strip()
        }
        | {
            skill.strip()
            for work in profile.work_experience
            for skill in work.skills
            if skill.strip()
        }
    )
    if preferences.get("TARGET_ROLES"):
        sections.append(f"Target roles: {preferences['TARGET_ROLES']}")
    if skills:
        sections.append("Skills: " + ", ".join(skills[:100]))
    for work in profile.work_experience[:8]:
        value = " ".join(
            part for part in (work.title or "", work.description or "") if part
        )
        if value:
            sections.append("Work experience: " + value[:1500])
    for project in profile.projects[:8]:
        value = " ".join(
            part for part in (project.name, project.description or "") if part
        )
        if value:
            sections.append("Project: " + value[:1500])
    for education in profile.education[:4]:
        value = " ".join(
            part
            for part in (
                education.degree or "",
                education.major or "",
                education.specialization or "",
            )
            if part
        )
        if value:
            sections.append("Education: " + value)
    if preferences.get("TARGET_LOCATIONS"):
        sections.append(f"Preferred locations: {preferences['TARGET_LOCATIONS']}")
    if preferences.get("WORK_MODE"):
        sections.append(f"Preferred work mode: {preferences['WORK_MODE']}")
    return "\n".join(sections)[:12000]


def build_public_document(row: dict[str, Any]) -> RecommendationDocument:
    skills = sorted(set(row.get("skill_tags") or []))
    requirements = sorted(set(row.get("requirement_tags") or []))
    preferred: list[str] = []
    fields = [
        f"Title: {row.get('title') or ''}",
        f"Company: {row.get('company_name') or ''}",
        f"Role family: {row.get('job_category') or row.get('job_function') or ''}",
        "Skills: " + ", ".join(skills),
        "Requirements: " + ", ".join(requirements),
        f"Location: {row.get('location_text') or ''}",
        f"Work mode: {row.get('work_mode') or ''}",
        f"Opportunity type: {row.get('opportunity_type') or ''}",
        "Description:\n" + (row.get("description") or ""),
    ]
    document_text = "\n".join(fields)
    content_hash = hashlib.sha256(
        (DOCUMENT_VERSION + "\x1f" + document_text).encode("utf-8")
    ).hexdigest()
    metadata = {
        "company": row.get("company_name"),
        "location": row.get("location_text"),
        "work_mode": row.get("work_mode"),
        "opportunity_type": row.get("opportunity_type"),
        "date_posted": str(row.get("date_posted") or ""),
    }
    return RecommendationDocument(
        source_job_id=str(row["public_id"]),
        public_job_id=str(row["public_id"]),
        content_hash=content_hash,
        title=row.get("title") or "Untitled",
        role_family=row.get("job_category") or row.get("job_function"),
        normalized_skills=skills,
        required_skills=requirements,
        preferred_skills=preferred,
        document_text=document_text,
        metadata=metadata,
        chunks=chunk_document(document_text),
    )


def infer_waterloo_opportunity_type(boards: list[str] | None) -> str | None:
    normalized = {value.strip().lower() for value in boards or [] if value.strip()}
    if normalized & {"full_cycle", "employer_student_direct"}:
        return "internship"
    if "graduating" in normalized:
        return "full_time"
    if "contract" in normalized:
        return "contract"
    return None


def build_waterloo_document(row: dict[str, Any]) -> RecommendationDocument:
    opportunity_type = infer_waterloo_opportunity_type(row.get("boards"))
    skills = sorted(
        {
            value.strip().lower()
            for value in (row.get("division"), *(row.get("boards") or []))
            if value and value.strip()
        }
    )
    fields = [
        f"Title: {row.get('title') or ''}",
        f"Organization: {row.get('organization') or ''}",
        f"Division: {row.get('division') or ''}",
        "Tags: " + ", ".join(skills),
        f"Location: {row.get('location_text') or ''}",
        f"Work mode: {row.get('work_mode') or ''}",
        f"Opportunity type: {opportunity_type or ''}",
        "Description:\n" + (row.get("description") or ""),
    ]
    document_text = "\n".join(fields)
    content_hash = hashlib.sha256(
        (DOCUMENT_VERSION + "\x1f" + document_text).encode("utf-8")
    ).hexdigest()
    metadata = {
        "company": row.get("organization"),
        "location": row.get("location_text"),
        "work_mode": row.get("work_mode"),
        "opportunity_type": opportunity_type,
        "date_posted": row.get("date_posted"),
        "application_deadline": row.get("application_deadline"),
        "application_url": row.get("application_url"),
        "division": row.get("division"),
        "boards": row.get("boards") or [],
    }
    return RecommendationDocument(
        source_job_id=str(row["source_job_id"]),
        public_job_id=None,
        content_hash=content_hash,
        title=row.get("title") or "Untitled",
        role_family=row.get("division"),
        normalized_skills=skills,
        required_skills=[],
        preferred_skills=[],
        document_text=document_text,
        metadata=metadata,
        chunks=chunk_document(document_text),
    )


def chunk_document(text: str) -> list[str]:
    normalized = re.sub(r"[ \t]+", " ", text).strip()
    words = normalized.split()
    if not words:
        return []
    chunks: list[str] = []
    step = CHUNK_WORDS - CHUNK_OVERLAP_WORDS
    for start in range(0, len(words), step):
        chunk = " ".join(words[start : start + CHUNK_WORDS])
        if chunk:
            chunks.append(chunk)
        if start + CHUNK_WORDS >= len(words):
            break
    return chunks


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(format(value, ".9g") for value in vector) + "]"


def stable_metadata_json(metadata: dict[str, Any]) -> str:
    return json.dumps(metadata, ensure_ascii=False, sort_keys=True, default=str)
