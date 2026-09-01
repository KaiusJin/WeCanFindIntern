"""Domain tool layer for the AI Agent.

Read tools execute immediately and return structured results. Write tools have
two phases: ``plan`` (resolve references and produce a preview, no mutation) and
``execute`` (perform the confirmed mutation). The orchestrator never executes a
write tool without a recorded user approval.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from wecanfindintern.agent.catalog import TOOL_CATALOG
from wecanfindintern.agent.contracts import AgentDeps, ToolError
from wecanfindintern.agent.contracts import LlmConfig as LlmConfig
from wecanfindintern.agent.job_access import (
    public_job_summary as _public_job_summary,
)
from wecanfindintern.agent.job_access import (
    resolve_job as _resolve_job,
)
from wecanfindintern.agent.job_access import (
    tracked_application_by_ref as _tracked_application_by_ref,
)
from wecanfindintern.agent.job_access import (
    tracked_external_map as _tracked_external_map,
)
from wecanfindintern.agent.job_access import (
    tracked_public_map as _tracked_public_map,
)
from wecanfindintern.agent.job_access import (
    waterlooworks_job_summary as _ww_job_summary,
)
from wecanfindintern.agent.models import (
    AddInterestedArgs,
    GenerateInterviewQuestionsArgs,
    GetJobDetailsArgs,
    JobReference,
    ListTrackerArgs,
    ProposeProfileUpdateArgs,
    RemoveInterestedArgs,
    SearchJobsArgs,
    UpdateProfileArgs,
    UpdateTrackerStageArgs,
)
from wecanfindintern.agent.recommend.tool import tool_recommend_jobs
from wecanfindintern.application.job_models import JobListFilters
from wecanfindintern.application.profile_context import profile_resume_text
from wecanfindintern.application.waterlooworks_tracker import (
    waterlooworks_tracker_fields,
)
from wecanfindintern.domain.classification import normalize_tag
from wecanfindintern.profile.models import ProfileBasics, ProfilePayload, UserProfile
from wecanfindintern.tracker.models import (
    TrackedApplication,
)


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
    normalized_category = normalize_tag(parsed.category) if parsed.category else None
    results: dict[str, list[dict[str, Any]]] = {}
    pagination: dict[str, dict[str, Any]] = {}
    used: list[str] = []
    if parsed.source in {"all", "public"}:
        filters = JobListFilters(
            query=parsed.query,
            company=parsed.company,
            city=parsed.city,
            country=parsed.country,
            region=parsed.region,
            skill=parsed.skill,
            category=normalized_category,
            work_modes=parsed.work_modes,
            opportunity_types=parsed.opportunity_types,
            recruiting_terms=parsed.recruiting_terms,
            posted_after=parsed.posted_after,
            cursor=(
                parsed.public_cursor
                or (parsed.cursor if parsed.source == "public" else None)
            ),
            sort_by_relevance=bool(parsed.query),
            limit=parsed.limit,
        )
        page = await deps.job_repo.list_jobs(filters)
        results["public"] = [_public_job_summary(item) for item in page.items]
        pagination["public"] = {
            "total": getattr(page, "total_count", len(page.items)),
            "has_more": getattr(page, "has_more", False),
            "next_cursor": getattr(page, "next_cursor", None),
        }
        used.append("public_jobs")
    if parsed.source in {"all", "waterloo_work"}:
        if parsed.recruiting_terms:
            results["waterloo_work"] = []
            pagination["waterloo_work"] = {
                "total": 0,
                "has_more": False,
                "next_cursor": None,
            }
            used.append("waterloo_work")
        else:
            ww = await deps.waterlooworks.list_jobs(
                query=parsed.query,
                company=parsed.company,
                skill=parsed.skill,
                category=normalized_category,
                city=parsed.city,
                region=parsed.region,
                country=parsed.country,
                work_modes=parsed.work_modes,
                opportunity_types=parsed.opportunity_types,
                posted_after=(
                    parsed.posted_after.isoformat() if parsed.posted_after else None
                ),
                limit=parsed.limit,
                cursor=(
                    parsed.waterloo_cursor
                    or (parsed.cursor if parsed.source == "waterloo_work" else None)
                ),
            )
            results["waterloo_work"] = [
                _ww_job_summary(item) for item in ww["items"]
            ]
            pagination["waterloo_work"] = {
                "total": ww.get("total_count", len(ww["items"])),
                "has_more": bool(ww.get("has_more")),
                "next_cursor": ww.get("next_cursor"),
            }
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
        "pagination": pagination,
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
                    **waterlooworks_tracker_fields(job)
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


async def tool_generate_interview_questions(
    args: dict[str, Any], deps: AgentDeps, phase: str
) -> dict[str, Any]:
    """Generate mock interview questions for one job or a raw description."""

    from wecanfindintern.interview.service import generate_interview_questions

    parsed = GenerateInterviewQuestionsArgs.model_validate(args)
    if deps.llm_config is None:
        raise ToolError("llm_config_missing", "AI model configuration is required.")
    description = (parsed.job_description or "").strip()
    resolved_label = "the provided description"
    if not description:
        if not parsed.job_id:
            raise ToolError(
                "invalid_arguments",
                "Provide job_id (resolved via search_jobs/get_job_details) or a "
                "job_description.",
            )
        job = await _resolve_job(
            JobReference(job_id=parsed.job_id, source=parsed.source), deps
        )
        if job is None:
            raise ToolError("job_not_found", f"Job {parsed.source}:{parsed.job_id} was not found.")
        description = job.get("description") or ""
        resolved_label = f"{job.get('title') or 'the job'} at {job.get('company') or 'unknown'}"
        if not description:
            raise ToolError(
                "description_missing",
                f"{resolved_label} has no stored job description to derive questions from.",
            )
    profile = await deps.profile_repo.get_profile()
    resume_text = profile_resume_text(profile)
    if not resume_text:
        raise ToolError(
            "profile_missing",
            "Interview questions need candidate context, but the Profile is empty. "
            "Ask the user to fill their Profile first.",
        )
    try:
        response = await asyncio.to_thread(
            generate_interview_questions,
            job_description=description,
            resume_text=resume_text,
            provider=deps.llm_config.provider,
            model_name=deps.llm_config.model_name,
            api_key=deps.llm_config.api_key,
            api_base=deps.llm_config.api_base,
        )
    except Exception as error:  # pragma: no cover - defensive
        raise ToolError("llm_failed", f"Interview question generation failed: {error}") from error
    if not response.ok:
        raise ToolError("llm_failed", response.error or "Interview question generation failed.")
    questions = [question.model_dump(mode="json") for question in response.questions]
    return {
        "ok": True,
        "data": {"questions": questions, "job": resolved_label},
        "used": [parsed.source if parsed.job_id else "description"],
        "summary": (
            f"Generated {len(questions)} mock interview question(s) for {resolved_label}."
        ),
        "for_llm": "\n".join(
            f"- [{question['category_label']}] {question['question']}"
            for question in questions
        )
        or "No questions generated.",
    }


TOOL_HANDLERS: dict[str, Any] = {
    "get_profile": tool_get_profile,
    "search_jobs": tool_search_jobs,
    "get_job_details": tool_get_job_details,
    "list_tracker": tool_list_tracker,
    "recommend_jobs": tool_recommend_jobs,
    "propose_profile_update": tool_propose_profile_update,
    "generate_interview_questions": tool_generate_interview_questions,
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
