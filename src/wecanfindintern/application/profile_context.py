"""Canonical profile-to-resume text projection shared by AI features."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any


def profile_resume_text(profile: Any) -> str:
    basics = getattr(profile, "basics", None)
    lines: list[str] = []
    if basics is not None and getattr(basics, "full_name", ""):
        lines.append(basics.full_name)
    if basics is not None:
        lines.extend(
            value
            for value in (
                getattr(basics, "email", None),
                getattr(basics, "phone", None),
                getattr(basics, "linkedin_url", None),
                getattr(basics, "github_url", None),
                getattr(basics, "portfolio_url", None),
            )
            if value
        )

    def append(
        title: str, entries: Iterable[Any], formatter: Callable[[Any], str]
    ) -> None:
        rendered = [formatter(entry) for entry in entries]
        rendered = [row for row in rendered if row]
        if rendered:
            lines.extend(("", title, *rendered))

    def joined(*parts: Any) -> str:
        return " | ".join(str(part) for part in parts if part not in (None, "", []))

    append(
        "Education",
        getattr(profile, "education", []) or [],
        lambda entry: joined(
            entry.institution,
            entry.degree,
            entry.major,
            entry.specialization,
            entry.minor,
            entry.location,
            entry.graduation_date_text,
            entry.gpa,
            *(entry.coursework or []),
        ),
    )
    append(
        "Work Experience",
        getattr(profile, "work_experience", []) or [],
        lambda entry: joined(
            entry.title,
            entry.company,
            entry.location,
            entry.employment_type,
            entry.start_date_text,
            entry.end_date_text,
            entry.description,
            *(entry.skills or []),
        ),
    )
    append(
        "Projects",
        getattr(profile, "projects", []) or [],
        lambda entry: joined(
            entry.name,
            entry.description,
            entry.project_url,
            entry.github_url,
            *(entry.skills or []),
        ),
    )
    append("Skills", getattr(profile, "skills", []) or [], lambda entry: entry.name)
    append(
        "Certifications",
        getattr(profile, "certifications", []) or [],
        lambda entry: joined(
            entry.name,
            entry.issuer,
            entry.issue_date_text,
            entry.expiry_date_text,
            entry.credential_id,
            entry.credential_url,
        ),
    )
    append(
        "Languages",
        getattr(profile, "languages", []) or [],
        lambda entry: joined(entry.name, entry.proficiency),
    )
    append(
        "Awards",
        getattr(profile, "awards", []) or [],
        lambda entry: joined(entry.title, entry.issuer, entry.date_text, entry.description),
    )
    return "\n".join(lines).strip()
