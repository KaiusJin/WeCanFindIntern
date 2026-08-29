"""Domain tool layer for the AI Agent.

Read tools execute immediately and return structured results. Write tools have
two phases: ``plan`` (resolve references and produce a preview, no mutation) and
``execute`` (perform the confirmed mutation). The orchestrator never executes a
write tool without a recorded user approval.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from wecanfindintern.agent.models import (
    AddInterestedArgs,
    GetJobDetailsArgs,
    JobReference,
    ListTrackerArgs,
    ProposeProfileUpdateArgs,
    RecommendJobsArgs,
    RemoveInterestedArgs,
    SearchJobsArgs,
    UpdateProfileArgs,
    UpdateTrackerStageArgs,
)
from wecanfindintern.api.models import JobDetail, JobListFilters, JobListItem
from wecanfindintern.db.read_repository import JobReadRepository
from wecanfindintern.profile.models import ProfileBasics, ProfilePayload, UserProfile
from wecanfindintern.profile.repository import ProfileRepository
from wecanfindintern.tracker.models import (
    TrackedApplication,
)
from wecanfindintern.tracker.repository import TrackerRepository


class ToolError(RuntimeError):
    """A tool-level failure with a stable error type for the UI and audit log."""

    def __init__(self, error_type: str, message: str) -> None:
        self.error_type = error_type
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class LlmConfig:
    provider: str
    model_name: str
    api_key: str
    api_base: str | None = None


@dataclass(slots=True)
class AgentDeps:
    job_repo: JobReadRepository
    tracker_repo: TrackerRepository
    profile_repo: ProfileRepository
    waterlooworks: Any
    llm_config: LlmConfig | None = None
    memory: Any = None


def _public_job_summary(job: JobListItem, *, description: str | None = None) -> dict[str, Any]:
    location = job.location.display_name if job.location else None
    return {
        "source": "public",
        "job_id": str(job.id),
        "title": job.title,
        "company": job.company_name,
        "location": location,
        "work_mode": job.work_mode,
        "opportunity_type": job.opportunity_type,
        "recruiting_term": (
            job.recruiting_term.display_name if job.recruiting_term else None
        ),
        "date_posted": job.date_posted.isoformat() if job.date_posted else None,
        "skill_tags": job.skill_tags[:20],
        "description": description,
    }


def _ww_job_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "waterloo_work",
        "job_id": item.get("source_job_id"),
        "title": item.get("title"),
        "company": item.get("organization"),
        "division": item.get("division"),
        "location": item.get("location_text"),
        "work_mode": item.get("work_mode"),
        "date_posted": item.get("date_posted"),
        "application_deadline": item.get("application_deadline"),
        "application_url": item.get("application_url"),
        "boards": item.get("boards") or [],
        "description": item.get("description"),
    }


def profile_summary(profile: UserProfile) -> dict[str, Any]:
    return {
        "basics": profile.basics.model_dump(),
        "completion_percent": profile.completion_percent,
        "education": [
            {
                "institution": entry.institution,
                "degree": entry.degree,
                "major": entry.major,
                "graduation_year": entry.graduation_year,
                "expected_graduation": entry.expected_graduation,
            }
            for entry in profile.education
        ],
        "work_experience": [
            {"company": entry.company, "title": entry.title, "is_current": entry.is_current}
            for entry in profile.work_experience
        ],
        "projects": [
            {"name": entry.name, "skills": entry.skills[:20]} for entry in profile.projects
        ],
        "skills": [entry.name for entry in profile.skills][:100],
        "certifications": [entry.name for entry in profile.certifications],
        "languages": [entry.name for entry in profile.languages],
        "awards": [entry.title for entry in profile.awards],
    }


async def _resolve_job(
    ref: JobReference, deps: AgentDeps
) -> dict[str, Any] | None:
    if ref.source == "public":
        try:
            job = await deps.job_repo.get_job(UUID(ref.job_id))
        except (ValueError, TypeError):
            raise ToolError(
                "invalid_job_id", f"Invalid public job ID: {ref.job_id}"
            ) from None
        if job is None:
            return None
        return _public_job_summary(job, description=job.description)
    item = await deps.waterlooworks.get_job(ref.job_id)
    if item is None:
        return None
    return _ww_job_summary(item)


async def _tracked_public_map(deps: AgentDeps) -> dict[str, str]:
    states = await deps.tracker_repo.list_tracked_job_states()
    return {str(state.job_id): state.stage for state in states}


async def _tracked_external_map(deps: AgentDeps) -> dict[str, str]:
    rows = await deps.tracker_repo.list_tracked_external_states()
    return {
        row["external_job_id"]: row["stage"]
        for row in rows
        if row.get("source") == "waterloo_work" and row.get("external_job_id")
    }


async def _tracked_application_by_ref(
    ref: JobReference, deps: AgentDeps
) -> TrackedApplication | None:
    if ref.source == "public":
        states = await deps.tracker_repo.list_tracked_job_states()
        for state in states:
            if str(state.job_id) == ref.job_id:
                return await deps.tracker_repo.get_application(state.application_id)
        return None
    rows = await deps.tracker_repo.list_tracked_external_states()
    for row in rows:
        if (
            row.get("source") == "waterloo_work"
            and row.get("external_job_id") == ref.job_id
        ):
            return await deps.tracker_repo.get_application(row["application_id"])
    return None


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


async def tool_get_profile(args: dict[str, Any], deps: AgentDeps, phase: str) -> dict[str, Any]:
    profile = await deps.profile_repo.get_profile()
    summary = profile_summary(profile)
    return {
        "ok": True,
        "data": summary,
        "used": ["profile"],
        "summary": (
            f"Profile loaded: {summary['basics'].get('full_name') or 'no name'}, "
            f"{len(summary['skills'])} skills, {len(summary['education'])} education entries, "
            f"{len(summary['work_experience'])} work entries, "
            f"{profile.completion_percent}% complete."
        ),
    }


async def tool_search_jobs(args: dict[str, Any], deps: AgentDeps, phase: str) -> dict[str, Any]:
    parsed = SearchJobsArgs.model_validate(args)
    results: dict[str, list[dict[str, Any]]] = {}
    used: list[str] = []
    if parsed.source in {"all", "public"}:
        company_filter = parsed.company
        filters = JobListFilters(
            query=parsed.query,
            city=parsed.city,
            country=parsed.country,
            region=parsed.region,
            skill=parsed.skill,
            category=parsed.category,
            limit=100,
        )
        page = await deps.job_repo.list_jobs(filters)
        items = page.items
        if company_filter:
            needle = company_filter.strip().lower()
            items = [
                item
                for item in items
                if item.company_name and needle in item.company_name.lower()
            ]
        results["public"] = [
            _public_job_summary(item) for item in items[: parsed.limit]
        ]
        used.append("public_jobs")
    if parsed.source in {"all", "waterloo_work"}:
        ww = await deps.waterlooworks.list_jobs(
            query=parsed.query or parsed.company,
            limit=parsed.limit,
        )
        results["waterloo_work"] = [_ww_job_summary(item) for item in ww["items"]]
        used.append("waterloo_work")
    total = sum(len(items) for items in results.values())
    lines: list[str] = []
    for source, items in results.items():
        for item in items[:10]:
            lines.append(
                f"- [{source}:{item.get('job_id')}] {item.get('title')} at "
                f"{item.get('company') or 'unknown company'} | "
                f"{item.get('location') or 'unknown location'}"
            )
    return {
        "ok": True,
        "data": results,
        "used": used,
        "summary": f"Found {total} job(s) matching your criteria.",
        "for_llm": "\n".join(lines) or "No jobs found.",
    }


async def tool_get_job_details(
    args: dict[str, Any], deps: AgentDeps, phase: str
) -> dict[str, Any]:
    parsed = GetJobDetailsArgs.model_validate(args)
    job = await _resolve_job(
        JobReference(job_id=parsed.job_id, source=parsed.source), deps
    )
    if job is None:
        raise ToolError("job_not_found", f"Job {parsed.source}:{parsed.job_id} was not found.")
    return {
        "ok": True,
        "data": job,
        "used": [parsed.source],
        "summary": f"Loaded {job['title']} at {job.get('company') or 'unknown company'}.",
    }


async def tool_list_tracker(args: dict[str, Any], deps: AgentDeps, phase: str) -> dict[str, Any]:
    parsed = ListTrackerArgs.model_validate(args)
    items, total = await deps.tracker_repo.list_applications(
        query=parsed.query,
        stage=parsed.stage,
        page_size=min(parsed.limit, 100),
    )
    stats = await deps.tracker_repo.get_stats()
    rows = [
        {
            "application_id": str(item.id),
            "company": item.company_name,
            "title": item.title,
            "stage": item.stage.value,
            "source": item.source.value,
            "job_id": str(item.job_id) if item.job_id else None,
            "external_job_id": item.external_job_id,
            "updated_at": item.updated_at.isoformat(),
        }
        for item in items
    ]
    return {
        "ok": True,
        "data": {"items": rows, "total": total, "stats": stats.model_dump()},
        "used": ["tracker"],
        "summary": (
            f"Tracker: {total} application(s)"
            + (f" in stage '{parsed.stage.value}'" if parsed.stage else "")
            + "."
        ),
        "for_llm": "\n".join(
            f"- [{row['application_id']}] {row['title']} at {row['company']} | "
            f"stage {row['stage']} | source {row['source']}"
            for row in rows[:15]
        )
        or "No tracker records found.",
    }


def _profile_skill_set(profile: UserProfile) -> set[str]:
    skills = {entry.name.strip().lower() for entry in profile.skills if entry.name}
    for entry in profile.projects:
        skills.update(s.strip().lower() for s in entry.skills if s)
    for entry in profile.work_experience:
        skills.update(s.strip().lower() for s in entry.skills if s)
    return {s for s in skills if s}


def _score_job(
    skills: set[str],
    title: str,
    tags: list[str],
    description: str | None,
) -> tuple[int, list[str]]:
    matched: list[str] = []
    haystack = " ".join([title or "", *(tags or []), description or ""]).lower()
    for skill in sorted(skills):
        if skill in haystack:
            matched.append(skill)
    title_lower = (title or "").lower()
    title_matches = [skill for skill in matched if skill in title_lower]
    score = len(matched) * 2 + len(title_matches) * 3
    return score, matched


def _preference_matches(
    preferences: dict[str, str], candidate: dict[str, Any]
) -> list[str]:
    """Return stated preferences this candidate satisfies (boost signals)."""

    matches: list[str] = []
    title = (candidate.get("title") or "").lower()
    location = (candidate.get("location_text") or "").lower()
    work_mode = (candidate.get("work_mode") or "").lower()

    target_locations = preferences.get("TARGET_LOCATIONS", "").strip()
    if target_locations:
        for token in (part.strip().lower() for part in target_locations.split(",")):
            if token and token in location:
                matches.append(f"location {token.title()}")
                break

    target_roles = preferences.get("TARGET_ROLES", "").strip()
    if target_roles:
        for token in (part.strip().lower() for part in target_roles.split(",")):
            if token and token in title:
                matches.append(f"role {token.title()}")
                break

    work_mode_pref = preferences.get("WORK_MODE", "").strip().upper()
    if work_mode_pref and work_mode_pref != "ANY" and work_mode:
        if work_mode_pref == "ONSITE" and work_mode in {"onsite", "in_person"}:
            matches.append("work mode in-person")
        elif work_mode == work_mode_pref.lower():
            matches.append(f"work mode {work_mode_pref.lower()}")
    return matches


async def tool_recommend_jobs(
    args: dict[str, Any], deps: AgentDeps, phase: str
) -> dict[str, Any]:
    parsed = RecommendJobsArgs.model_validate(args)
    profile = await deps.profile_repo.get_profile()
    skills = _profile_skill_set(profile)
    used: list[str] = ["profile"]
    candidates: list[dict[str, Any]] = []
    preferences: dict[str, str] = {}
    if deps.memory is not None:
        try:
            preferences = await deps.memory.get_preferences()
        except Exception:  # pragma: no cover - memory is advisory
            preferences = {}

    if parsed.source in {"all", "public"}:
        page = await deps.job_repo.list_jobs(JobListFilters(limit=100))
        used.append("public_jobs")
        for item in page.items:
            detail: JobDetail | None = None
            description: str | None = None
            application_url: str | None = None
            with suppress(ValueError, TypeError):
                detail = await deps.job_repo.get_job(item.id)
            if detail is not None:
                description = detail.description
                sources = getattr(detail, "sources", None) or []
                if sources:
                    application_url = (
                        sources[0].direct_url or sources[0].url
                    )
            score, matched = _score_job(
                skills, item.title, item.skill_tags + item.display_tags, description
            )
            candidates.append(
                {
                    "source": "public",
                    "job_id": str(item.id),
                    "title": item.title,
                    "company": item.company_name,
                    "location": item.location.display_name if item.location else None,
                    "work_mode": item.work_mode,
                    "recruiting_term": (
                        item.recruiting_term.display_name if item.recruiting_term else None
                    ),
                    "date_posted": item.date_posted.isoformat() if item.date_posted else None,
                    "application_deadline": None,
                    "application_url": application_url,
                    "score": score,
                    "matched_skills": matched[:10],
                    "description_available": bool(description),
                    "location_text": (
                        item.location.display_name if item.location else None
                    ),
                }
            )
    if parsed.source in {"all", "waterloo_work"}:
        ww = await deps.waterlooworks.list_jobs(
            limit=100,
            include_description=True,
        )
        used.append("waterloo_work")
        for item in ww["items"]:
            score, matched = _score_job(
                skills,
                item.get("title") or "",
                [item.get("division") or ""],
                item.get("description"),
            )
            candidates.append(
                {
                    "source": "waterloo_work",
                    "job_id": item.get("source_job_id"),
                    "title": item.get("title"),
                    "company": item.get("organization"),
                    "location": item.get("location_text"),
                    "work_mode": item.get("work_mode"),
                    "application_deadline": item.get("application_deadline"),
                    "application_url": item.get("application_url"),
                    "score": score,
                    "matched_skills": matched[:10],
                    "description_available": bool(item.get("description")),
                    "location_text": item.get("location_text"),
                }
            )

    for candidate in candidates:
        pref_matches = _preference_matches(preferences, candidate)
        if pref_matches:
            candidate["score"] += 4 * len(pref_matches)
            candidate["preference_matches"] = pref_matches

    candidates.sort(key=lambda c: c["score"], reverse=True)
    recommendations: list[dict[str, Any]] = []
    for candidate in candidates[: parsed.limit]:
        reasons: list[str] = []
        if candidate["matched_skills"]:
            reasons.append(
                "Matches profile skills: " + ", ".join(candidate["matched_skills"])
            )
        if not candidate["matched_skills"]:
            reasons.append("Limited overlap with your profile skills.")
        if not candidate.get("description_available"):
            reasons.append("Insufficient job description to judge deeply.")
        for match in candidate.get("preference_matches") or []:
            reasons.append(f"Matches your stated preference: {match}")
        recommendations.append({**candidate, "reasons": reasons})

    return {
        "ok": True,
        "data": {
            "recommendations": recommendations,
            "profile_used": {
                "skills": sorted(skills)[:50],
                "completion_percent": profile.completion_percent,
            },
        },
        "used": used,
        "summary": (
            f"Recommended {len(recommendations)} job(s) based on "
            f"{len(skills)} profile skills."
        ),
        "for_llm": "\n".join(
            f"- [{r['source']}:{r['job_id']}] {r['title']} at {r['company'] or 'unknown'} | "
            f"{r.get('location') or 'unknown location'} | "
            f"deadline: {r.get('application_deadline') or 'not specified'} | "
            f"link: {r.get('application_url') or 'not available'} | matched: "
            f"{', '.join(r['matched_skills']) or 'none'} | "
            f"preferences: {', '.join(r.get('preference_matches') or []) or 'none'} | "
            f"{', '.join(r['reasons'])}"
            for r in recommendations
        )
        or "No recommendations available.",
    }


# ---------------------------------------------------------------------------
# Write tools (plan -> preview, execute -> mutation)
# ---------------------------------------------------------------------------


async def tool_add_interested(
    args: dict[str, Any], deps: AgentDeps, phase: str
) -> dict[str, Any]:
    parsed = AddInterestedArgs.model_validate(args)
    public_tracked = await _tracked_public_map(deps)
    external_tracked = await _tracked_external_map(deps)

    resolved: list[dict[str, Any]] = []
    for ref in parsed.jobs:
        job = await _resolve_job(ref, deps)
        if job is None:
            resolved.append(
                {"job_id": ref.job_id, "source": ref.source, "error": "job_not_found"}
            )
            continue
        tracked = (
            ref.job_id in public_tracked
            if ref.source == "public"
            else ref.job_id in external_tracked
        )
        resolved.append(
            {
                "job_id": ref.job_id,
                "source": ref.source,
                "title": job["title"],
                "company": job.get("company"),
                "already_tracked": tracked,
            }
        )

    if phase == "plan":
        return {
            "ok": True,
            "requires_approval": True,
            "preview": {
                "action": "add_interested",
                "jobs": resolved,
                "count": len(resolved),
            },
            "summary": (
                f"Adding {len(resolved)} job(s) to Interested"
                + (
                    f" ({sum(1 for r in resolved if r.get('already_tracked'))} already tracked)"
                    if any(r.get("already_tracked") for r in resolved)
                    else ""
                )
                + "."
            ),
        }

    results: list[dict[str, Any]] = []
    for entry in resolved:
        if entry.get("error"):
            results.append(
                {
                    "job_id": entry["job_id"],
                    "source": entry["source"],
                    "status": "failed",
                    "error": entry["error"],
                }
            )
            continue
        if entry["already_tracked"]:
            results.append(
                {
                    "job_id": entry["job_id"],
                    "source": entry["source"],
                    "title": entry["title"],
                    "company": entry.get("company"),
                    "status": "already_interested",
                }
            )
            continue
        ref = JobReference(job_id=entry["job_id"], source=entry["source"])
        job = await _resolve_job(ref, deps)
        if job is None:
            results.append(
                {
                    "job_id": entry["job_id"],
                    "source": entry["source"],
                    "status": "failed",
                    "error": "job_not_found",
                }
            )
            continue
        try:
            if ref.source == "public":
                created = await deps.tracker_repo.bookmark_job(UUID(ref.job_id))
            else:
                created = await deps.tracker_repo.bookmark_waterlooworks_job(
                    source_job_id=ref.job_id,
                    company_name=job.get("company") or "Company not specified",
                    title=job.get("title") or "Untitled role",
                    location_text=job.get("location"),
                    work_mode=job.get("work_mode"),
                    job_url=job.get("application_url"),
                    job_description=job.get("description"),
                    application_deadline=job.get("application_deadline"),
                )
            results.append(
                {
                    "job_id": ref.job_id,
                    "source": ref.source,
                    "title": entry["title"],
                    "company": entry.get("company"),
                    "status": "added" if created else "failed",
                    "application_id": str(created.id) if created else None,
                }
            )
        except Exception as error:  # pragma: no cover - defensive
            results.append(
                {
                    "job_id": ref.job_id,
                    "source": ref.source,
                    "status": "failed",
                    "error": str(error),
                }
            )
    added = sum(1 for r in results if r["status"] == "added")
    already = sum(1 for r in results if r["status"] == "already_interested")
    failed = sum(1 for r in results if r["status"] == "failed")
    return {
        "ok": True,
        "data": {"results": results},
        "summary": f"Interested: {added} added, {already} already tracked, {failed} failed.",
    }


async def _resolve_tracker_targets(
    application_ids: list[str],
    job_references: list[JobReference],
    deps: AgentDeps,
) -> list[TrackedApplication]:
    targets: list[TrackedApplication] = []
    seen: set[str] = set()
    for raw_id in application_ids:
        try:
            item = await deps.tracker_repo.get_application(UUID(raw_id))
        except (ValueError, TypeError):
            continue
        if item is not None and str(item.id) not in seen:
            targets.append(item)
            seen.add(str(item.id))
    for ref in job_references:
        item = await _tracked_application_by_ref(ref, deps)
        if item is not None and str(item.id) not in seen:
            targets.append(item)
            seen.add(str(item.id))
    return targets


async def tool_update_tracker_stage(
    args: dict[str, Any], deps: AgentDeps, phase: str
) -> dict[str, Any]:
    parsed = UpdateTrackerStageArgs.model_validate(args)
    if not parsed.application_ids and not parsed.job_references:
        raise ToolError("missing_targets", "No tracker records or job references were provided.")
    targets = await _resolve_tracker_targets(
        parsed.application_ids, parsed.job_references, deps
    )
    rows = [
        {
            "application_id": str(item.id),
            "title": item.title,
            "company": item.company_name,
            "current_stage": item.stage.value,
            "new_stage": parsed.stage.value,
        }
        for item in targets
    ]
    if not rows:
        return {
            "ok": True,
            "data": {"results": [], "message": "No matching tracker records found."},
            "used": ["tracker"],
            "summary": "No matching tracker records were found.",
        }
    if phase == "plan":
        return {
            "ok": True,
            "requires_approval": True,
            "preview": {
                "action": "update_tracker_stage",
                "stage": parsed.stage.value,
                "records": rows,
                "count": len(rows),
            },
            "summary": (
                f"Changing {len(rows)} tracker record(s) to stage "
                f"'{parsed.stage.value}'."
            ),
        }

    to_change = [row for row in rows if row["current_stage"] != row["new_stage"]]
    changed_ids = [UUID(row["application_id"]) for row in to_change]
    updated = 0
    if changed_ids:
        updated = await deps.tracker_repo.bulk_update(changed_ids, stage=parsed.stage)
    results = [
        {
            "application_id": row["application_id"],
            "title": row["title"],
            "company": row["company"],
            "status": (
                "updated"
                if row["current_stage"] != row["new_stage"]
                else "unchanged"
            ),
        }
        for row in rows
    ]
    return {
        "ok": True,
        "data": {"results": results, "updated": updated},
        "summary": f"Tracker stage updated for {updated} record(s).",
    }


async def tool_remove_interested(
    args: dict[str, Any], deps: AgentDeps, phase: str
) -> dict[str, Any]:
    parsed = RemoveInterestedArgs.model_validate(args)
    public_tracked = await _tracked_public_map(deps)
    external_tracked = await _tracked_external_map(deps)

    resolved: list[dict[str, Any]] = []
    for ref in parsed.jobs:
        stage = (
            public_tracked.get(ref.job_id)
            if ref.source == "public"
            else external_tracked.get(ref.job_id)
        )
        job = await _resolve_job(ref, deps)
        resolved.append(
            {
                "job_id": ref.job_id,
                "source": ref.source,
                "title": (job or {}).get("title"),
                "company": (job or {}).get("company"),
                "tracked_stage": stage,
                "protected": stage is not None and stage != "interested",
                "not_tracked": stage is None,
            }
        )

    if phase == "plan":
        protected = [r for r in resolved if r["protected"]]
        return {
            "ok": True,
            "requires_approval": True,
            "preview": {
                "action": "remove_interested",
                "jobs": resolved,
                "protected_count": len(protected),
            },
            "summary": (
                f"Removing {len(resolved)} job(s) from Interested"
                + (
                    f"; {len(protected)} protected because they are past Interested"
                    if protected
                    else ""
                )
                + "."
            ),
        }

    results: list[dict[str, Any]] = []
    for entry in resolved:
        if entry["not_tracked"]:
            results.append(
                {
                    "job_id": entry["job_id"],
                    "source": entry["source"],
                    "status": "not_found",
                }
            )
            continue
        if entry["protected"]:
            results.append(
                {
                    "job_id": entry["job_id"],
                    "source": entry["source"],
                    "title": entry.get("title"),
                    "status": "protected",
                    "stage": entry["tracked_stage"],
                }
            )
            continue
        try:
            if entry["source"] == "public":
                deleted, stage = await deps.tracker_repo.unbookmark_job(
                    UUID(entry["job_id"])
                )
            else:
                deleted, stage = await deps.tracker_repo.unbookmark_waterlooworks_job(
                    entry["job_id"]
                )
            if deleted:
                results.append(
                    {
                        "job_id": entry["job_id"],
                        "source": entry["source"],
                        "title": entry.get("title"),
                        "status": "removed",
                    }
                )
            else:
                results.append(
                    {
                        "job_id": entry["job_id"],
                        "source": entry["source"],
                        "title": entry.get("title"),
                        "status": "protected" if stage else "failed",
                        "stage": stage,
                    }
                )
        except Exception as error:  # pragma: no cover - defensive
            results.append(
                {
                    "job_id": entry["job_id"],
                    "source": entry["source"],
                    "status": "failed",
                    "error": str(error),
                }
            )
    removed = sum(1 for r in results if r["status"] == "removed")
    protected = sum(1 for r in results if r["status"] == "protected")
    missing = sum(1 for r in results if r["status"] == "not_found")
    return {
        "ok": True,
        "data": {"results": results},
        "summary": (
            f"Interested removal: {removed} removed, {protected} protected, "
            f"{missing} not found."
        ),
    }


def _basics_diff(
    current: UserProfile, proposed: ProfilePayload
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for field in ProfileBasics.model_fields:
        old = getattr(current.basics, field)
        new = getattr(proposed.basics, field)
        if old != new:
            changes.append(
                {
                    "section": "basics",
                    "field": field,
                    "old": old,
                    "new": new,
                }
            )
    return changes


def _section_diff(current: UserProfile, proposed: ProfilePayload) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    current_skills = sorted(s.name for s in current.skills)
    proposed_skills = sorted(s.name for s in proposed.skills)
    if current_skills != proposed_skills:
        changes.append(
            {
                "section": "skills",
                "field": "skills",
                "old": current_skills,
                "new": proposed_skills,
            }
        )
    current_edu = [e.institution for e in current.education]
    proposed_edu = [e.institution for e in proposed.education]
    if current_edu != proposed_edu:
        changes.append(
            {
                "section": "education",
                "field": "institutions",
                "old": current_edu,
                "new": proposed_edu,
            }
        )
    current_work = [f"{e.company} / {e.title}" for e in current.work_experience]
    proposed_work = [f"{e.company} / {e.title}" for e in proposed.work_experience]
    if current_work != proposed_work:
        changes.append(
            {
                "section": "work_experience",
                "field": "entries",
                "old": current_work,
                "new": proposed_work,
            }
        )
    current_projects = [p.name for p in current.projects]
    proposed_projects = [p.name for p in proposed.projects]
    if current_projects != proposed_projects:
        changes.append(
            {
                "section": "projects",
                "field": "names",
                "old": current_projects,
                "new": proposed_projects,
            }
        )
    return changes


def _merge_profile(current: UserProfile, proposed: ProfilePayload) -> ProfilePayload:
    """Merge a partial update payload onto the current profile.

    Sections the caller explicitly provided replace the current section; other
    sections and unspecified basics fields are preserved.
    """

    merged = current.model_dump(mode="json")
    for section in (
        "education",
        "work_experience",
        "projects",
        "skills",
        "certifications",
        "languages",
        "awards",
    ):
        if section in proposed.model_fields_set:
            merged[section] = [
                item.model_dump(mode="json")
                for item in getattr(proposed, section)
            ]
    if "basics" in proposed.model_fields_set:
        basics = proposed.basics
        for field, value in basics.model_dump(mode="json").items():
            if field in basics.model_fields_set:
                merged["basics"][field] = value
    return ProfilePayload.model_validate(merged)


async def tool_update_profile(
    args: dict[str, Any], deps: AgentDeps, phase: str
) -> dict[str, Any]:
    parsed = UpdateProfileArgs.model_validate(args)
    tolerated_meta = {"id", "completion_percent", "created_at", "updated_at"}
    try:
        payload_data = {
            key: value
            for key, value in parsed.payload.items()
            if key not in tolerated_meta
        }
        proposed = ProfilePayload.model_validate(payload_data)
    except Exception as error:
        raise ToolError("invalid_profile", f"Invalid profile payload: {error}") from error
    unknown_keys = set(payload_data) - set(ProfilePayload.model_fields)
    if unknown_keys:
        raise ToolError(
            "invalid_profile",
            "Unknown profile payload keys: "
            + ", ".join(sorted(unknown_keys))
            + ". Contact fields like email and city belong under 'basics'.",
        )
    if not proposed.model_fields_set:
        raise ToolError(
            "invalid_profile",
            "The profile payload is empty or has an invalid structure.",
        )
    current = await deps.profile_repo.get_profile()
    merged = _merge_profile(current, proposed)
    changes = _basics_diff(current, merged) + _section_diff(current, merged)
    preview = {
        "action": "update_profile",
        "changes": changes,
        "change_count": len(changes),
    }
    if phase == "plan":
        if not changes:
            return {
                "ok": True,
                "data": {"changes": [], "message": "No profile changes were detected."},
                "used": ["profile"],
                "summary": "No profile changes were detected.",
            }
        return {
            "ok": True,
            "requires_approval": True,
            "preview": preview,
            "summary": f"Profile update would change {len(changes)} field(s).",
        }
    saved = await deps.profile_repo.save_profile(merged)
    return {
        "ok": True,
        "data": {
            "changes": changes,
            "completion_percent": saved.completion_percent,
        },
        "summary": (
            f"Profile saved with {len(changes)} change(s); "
            f"completion now {saved.completion_percent}%."
        ),
    }


async def tool_propose_profile_update(
    args: dict[str, Any], deps: AgentDeps, phase: str
) -> dict[str, Any]:
    """Draft Profile changes from the user's request using the configured LLM."""

    import asyncio

    parsed = ProposeProfileUpdateArgs.model_validate(args)
    profile = await deps.profile_repo.get_profile()
    from wecanfindintern.llm.gateway import complete_json

    if deps.llm_config is None:
        raise ToolError("llm_config_missing", "AI model configuration is required.")
    try:
        result = await asyncio.to_thread(
            complete_json,
            provider=deps.llm_config.provider,
            model_name=deps.llm_config.model_name,
            api_key=deps.llm_config.api_key,
            system_prompt=(
                "You draft structured Profile changes for a job-seeker workspace. "
                "Output JSON only: {\"changes\": [{\"section\", \"field\", \"old_value\", "
                "\"new_value\", \"evidence\", \"confidence\"}], \"payload\": <full profile.v1 "
                "payload with the proposed changes applied>}. Keep the payload schema exactly "
                "profile.v1. Never invent personal facts without evidence; when the user's "
                "request is unsupported by the current profile, leave fields unchanged and "
                "list them in changes with confidence 0."
            ),
            user_prompt=(
                f"User request: {parsed.request}\n\n"
                f"Current profile JSON:\n{profile.model_dump_json(indent=2)}"
            ),
        )
        data = result.data
    except Exception as error:
        raise ToolError("llm_failed", f"Profile draft failed: {error}") from error

    changes = data.get("changes", []) if isinstance(data, dict) else []
    payload = data.get("payload")
    try:
        ProfilePayload.model_validate(payload)
    except Exception as error:
        raise ToolError("invalid_profile", f"LLM returned an invalid profile: {error}") from error
    return {
        "ok": True,
        "data": {
            "changes": changes,
            "payload": payload,
            "note": "This is a draft. Ask the agent to save it to apply changes.",
        },
        "used": ["profile"],
        "summary": (
            f"Drafted {len(changes)} profile change(s); review before saving."
        ),
    }


TOOL_CATALOG: list[dict[str, Any]] = [
    {
        "name": "get_profile",
        "description": (
            "Read the user's confirmed Profile (basics, education, work, projects, "
            "skills, certifications, languages, awards)."
        ),
        "parameters": {"type": "object", "properties": {}},
        "mutates": False,
    },
    {
        "name": "search_jobs",
        "description": (
            "Search jobs across the public library and WaterlooWorks. Returns title, "
            "company, location, source and job id."
        ),
        "parameters": SearchJobsArgs.model_json_schema(),
        "mutates": False,
    },
    {
        "name": "get_job_details",
        "description": (
            "Get full details for one job. Use job_id from search results; source is "
            "'public' for UUIDs or 'waterloo_work' for WaterlooWorks Job IDs."
        ),
        "parameters": GetJobDetailsArgs.model_json_schema(),
        "mutates": False,
    },
    {
        "name": "list_tracker",
        "description": "List application Tracker records, optionally filtered by stage or query.",
        "parameters": ListTrackerArgs.model_json_schema(),
        "mutates": False,
    },
    {
        "name": "recommend_jobs",
        "description": (
            "Recommend jobs based on the user's Profile using deterministic skill "
            "matching and explainable reasons. Never writes anything."
        ),
        "parameters": RecommendJobsArgs.model_json_schema(),
        "mutates": False,
    },
    {
        "name": "propose_profile_update",
        "description": (
            "Draft structured Profile changes from a user request. Read-only; returns a "
            "field-level draft with evidence and confidence."
        ),
        "parameters": ProposeProfileUpdateArgs.model_json_schema(),
        "mutates": False,
    },
    {
        "name": "add_interested",
        "description": (
            "Plan to add one or more jobs to the Tracker's Interested stage. Requires "
            "user confirmation before it runs."
        ),
        "parameters": AddInterestedArgs.model_json_schema(),
        "mutates": True,
    },
    {
        "name": "update_tracker_stage",
        "description": (
            "Plan to change one or more Tracker records to a new stage (interested, "
            "applied, interview, offer, rejected). Requires user confirmation."
        ),
        "parameters": UpdateTrackerStageArgs.model_json_schema(),
        "mutates": True,
    },
    {
        "name": "remove_interested",
        "description": (
            "Plan to remove one or more jobs from Interested. Records past Interested "
            "are protected and will not be removed. Requires user confirmation."
        ),
        "parameters": RemoveInterestedArgs.model_json_schema(),
        "mutates": True,
    },
    {
        "name": "update_profile",
        "description": (
            "Plan to save a full profile.v1 payload to the user's Profile. Requires "
            "user confirmation; a field-level diff is shown first."
        ),
        "parameters": UpdateProfileArgs.model_json_schema(),
        "mutates": True,
    },
]


TOOL_HANDLERS: dict[str, Any] = {
    "get_profile": tool_get_profile,
    "search_jobs": tool_search_jobs,
    "get_job_details": tool_get_job_details,
    "list_tracker": tool_list_tracker,
    "recommend_jobs": tool_recommend_jobs,
    "propose_profile_update": tool_propose_profile_update,
    "add_interested": tool_add_interested,
    "update_tracker_stage": tool_update_tracker_stage,
    "remove_interested": tool_remove_interested,
    "update_profile": tool_update_profile,
}


def is_write_tool(name: str) -> bool:
    for spec in TOOL_CATALOG:
        if spec["name"] == name:
            return bool(spec["mutates"])
    return False


async def run_tool(
    name: str,
    arguments: dict[str, Any],
    deps: AgentDeps,
    *,
    phase: str,
) -> dict[str, Any]:
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        raise ToolError("unknown_tool", f"Unknown tool: {name}")
    return await handler(arguments or {}, deps, phase)


def summarize_for_llm(result: dict[str, Any], limit: int = 2200) -> str:
    """Compact, safe textual summary of a tool result for the reply model."""

    if result.get("for_llm"):
        return str(result["for_llm"])[:limit]

    def text(value: Any, depth: int = 0) -> str:
        if depth > 3:
            return "..."
        if isinstance(value, dict):
            return " ".join(f"{k}={text(v, depth + 1)}" for k, v in value.items())
        if isinstance(value, list):
            return " | ".join(text(v, depth + 1) for v in value[:12])
        return str(value)

    return text(result)[:limit]
