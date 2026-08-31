"""Domain tool layer for the AI Agent.

Read tools execute immediately and return structured results. Write tools have
two phases: ``plan`` (resolve references and produce a preview, no mutation) and
``execute`` (perform the confirmed mutation). The orchestrator never executes a
write tool without a recorded user approval.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from wecanfindintern.agent.models import (
    AddInterestedArgs,
    GenerateInterviewQuestionsArgs,
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
from wecanfindintern.agent.recommend import (
    enforce_company_diversity,
    expand_target_roles,
    is_expired,
    recommendation_cache,
    rerank_with_llm,
    score_candidate,
    target_role_matches,
)
from wecanfindintern.agent.recommend.cache import build_cache_key
from wecanfindintern.agent.recommend.documents import (
    build_profile_query,
    infer_waterloo_opportunity_type,
)
from wecanfindintern.agent.recommend.embeddings import EmbeddingConfig, EmbeddingGateway
from wecanfindintern.agent.recommend.repository import (
    RecommendationFilters,
    RecommendationRepository,
)
from wecanfindintern.agent.recommend.scoring import ScoredCandidate
from wecanfindintern.api.models import JobDetail, JobListFilters, JobListItem
from wecanfindintern.db.read_repository import JobReadRepository
from wecanfindintern.domain.classification import normalize_for_matching
from wecanfindintern.domain.location import clean_location_display, parse_location
from wecanfindintern.profile.models import ProfileBasics, ProfilePayload, UserProfile
from wecanfindintern.profile.repository import ProfileRepository
from wecanfindintern.tracker.models import (
    TrackedApplication,
)
from wecanfindintern.tracker.repository import TrackerRepository

RECOMMENDATION_RANKING_VERSION = "recommend.v3"


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
    timeout_seconds: float = 15.0


@dataclass(slots=True)
class AgentDeps:
    job_repo: JobReadRepository
    tracker_repo: TrackerRepository
    profile_repo: ProfileRepository
    waterlooworks: Any
    llm_config: LlmConfig | None = None
    embedding_config: EmbeddingConfig | None = None
    memory: Any = None
    recommendation_repo: RecommendationRepository | None = None


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
        "location": clean_location_display(item.get("location_text")),
        "work_mode": item.get("work_mode"),
        "opportunity_type": item.get("opportunity_type")
        or infer_waterloo_opportunity_type(item.get("boards")),
        "date_posted": item.get("date_posted"),
        "application_deadline": item.get("application_deadline"),
        "application_url": item.get("application_url"),
        "boards": item.get("boards") or [],
        "description": item.get("description"),
    }


def _ww_matches_search(item: dict[str, Any], parsed: SearchJobsArgs) -> bool:
    parsed_location = parse_location(item.get("location_text"))
    company = (item.get("organization") or "").lower()
    if parsed.company and parsed.company.lower() not in company:
        return False
    city = item.get("city") or parsed_location.city or ""
    if parsed.city and parsed.city.lower() != city.lower():
        return False
    region_values = {
        str(value).lower()
        for value in (
            item.get("province"),
            parsed_location.region_code,
            parsed_location.region_name,
        )
        if value
    }
    if parsed.region and parsed.region.lower() not in region_values:
        return False
    country_values = {
        str(value).lower()
        for value in (
            item.get("country"),
            parsed_location.country_code,
            parsed_location.country_name,
        )
        if value
    }
    if parsed.country and parsed.country.lower() not in country_values:
        return False
    if parsed.work_modes and item.get("work_mode") not in parsed.work_modes:
        return False
    if parsed.posted_after and (item.get("date_posted") or "") < parsed.posted_after.isoformat():
        return False
    if parsed.recruiting_terms:
        return False  # WaterlooWorks records do not currently carry recruiting-term metadata.
    if parsed.opportunity_types:
        requested = {value.lower() for value in parsed.opportunity_types}
        inferred = item.get("opportunity_type") or infer_waterloo_opportunity_type(
            item.get("boards")
        )
        if inferred not in requested:
            return False
    return True


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
            category=parsed.category,
            work_modes=parsed.work_modes,
            opportunity_types=parsed.opportunity_types,
            recruiting_terms=parsed.recruiting_terms,
            posted_after=parsed.posted_after,
            cursor=parsed.cursor,
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
                "next_offset": None,
            }
            used.append("waterloo_work")
        else:
            ww = await deps.waterlooworks.list_jobs(
                query=parsed.query,
                company=parsed.company,
                city=parsed.city,
                region=parsed.region,
                country=parsed.country,
                work_modes=parsed.work_modes,
                opportunity_types=parsed.opportunity_types,
                posted_after=(
                    parsed.posted_after.isoformat() if parsed.posted_after else None
                ),
                limit=parsed.limit,
                offset=parsed.offset,
            )
            ww_items = [item for item in ww["items"] if _ww_matches_search(item, parsed)]
            results["waterloo_work"] = [
                _ww_job_summary(item) for item in ww_items[: parsed.limit]
            ]
            next_offset = parsed.offset + len(ww["items"])
            pagination["waterloo_work"] = {
                "total": ww.get("total"),
                "has_more": next_offset < int(ww.get("total") or 0),
                "next_offset": (
                    next_offset if next_offset < int(ww.get("total") or 0) else None
                ),
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


def _profile_skill_set(profile: UserProfile) -> set[str]:
    skills = {entry.name.strip().lower() for entry in profile.skills if entry.name}
    for entry in profile.projects:
        skills.update(s.strip().lower() for s in entry.skills if s)
    for entry in profile.work_experience:
        skills.update(s.strip().lower() for s in entry.skills if s)
    return {s for s in skills if s}


def _education_stage(profile: UserProfile) -> str:
    latest = profile.education[-1] if profile.education else None
    if latest is None:
        return ""
    parts = [latest.degree or "", latest.major or ""]
    if getattr(latest, "expected_graduation", None):
        parts.append(f"graduating {latest.expected_graduation}")
    return ", ".join(part for part in parts if part)


def _is_early_career_profile(profile: UserProfile) -> bool:
    if any(
        entry.expected_graduation or entry.status == "studying"
        for entry in profile.education
    ):
        return True
    senior_pattern = re.compile(
        r"\b(?:senior|sr\.?|staff|principal|lead|manager|director|architect|head)\b",
        re.IGNORECASE,
    )
    has_senior_work = any(
        senior_pattern.search(entry.title or "") for entry in profile.work_experience
    )
    return bool(profile.education) and len(profile.work_experience) <= 3 and not has_senior_work


def _cap_candidates_by_source(
    candidates: list[dict[str, Any]], *, limit: int
) -> list[dict[str, Any]]:
    if len(candidates) <= limit:
        return candidates

    def rank_key(candidate: dict[str, Any]) -> tuple[float, str]:
        return (
            float((candidate.get("retrieval") or {}).get("rrf_score", 0)),
            candidate.get("date_posted") or "",
        )

    groups: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        groups.setdefault(candidate.get("source") or "unknown", []).append(candidate)
    for values in groups.values():
        values.sort(key=rank_key, reverse=True)
    if len(groups) == 1:
        return next(iter(groups.values()))[:limit]

    selected: list[dict[str, Any]] = []
    quota = max(1, limit // len(groups))
    leftovers: list[dict[str, Any]] = []
    for values in groups.values():
        selected.extend(values[:quota])
        leftovers.extend(values[quota:])
    leftovers.sort(key=rank_key, reverse=True)
    selected.extend(leftovers[: max(0, limit - len(selected))])
    return selected[:limit]


def _preference_matches(
    preferences: dict[str, str], candidate: dict[str, Any]
) -> list[str]:
    """Return stated preferences this candidate satisfies (boost signals)."""

    matches: list[str] = []
    location = (candidate.get("location_text") or "").lower()
    work_mode = (candidate.get("work_mode") or "").lower()

    target_locations = preferences.get("TARGET_LOCATIONS", "").strip()
    if target_locations:
        for token in (part.strip().lower() for part in target_locations.split(",")):
            if token and token in location:
                matches.append(f"location {token.title()}")
                break

    target_roles = preferences.get("TARGET_ROLES", "").strip()
    roles = [part.strip() for part in target_roles.split(",") if part.strip()]
    if roles and target_role_matches(candidate.get("title"), roles):
        matches.append(f"role {roles[0].title()}")

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
    started = time.perf_counter()

    async def load_preferences() -> dict[str, str]:
        if deps.memory is None:
            return {}
        try:
            return await deps.memory.get_preferences()
        except Exception:  # pragma: no cover - preferences are advisory
            return {}

    async def load_library_version() -> str:
        method = getattr(deps.job_repo, "jobs_library_version", None)
        jobs_version = await method() if method is not None else "compat"
        if deps.recommendation_repo is None:
            return jobs_version
        try:
            rag_version = await deps.recommendation_repo.corpus_version()
        except Exception:
            rag_version = "unavailable"
        return f"{jobs_version}|rag:{rag_version}"

    profile, preferences, public_tracked, external_tracked, library_version = (
        await asyncio.gather(
            deps.profile_repo.get_profile(),
            load_preferences(),
            _tracked_public_map(deps),
            _tracked_external_map(deps),
            load_library_version(),
        )
    )
    skills = _profile_skill_set(profile)
    preferences = dict(preferences)
    if parsed.target_roles:
        preferences["TARGET_ROLES"] = ", ".join(parsed.target_roles)
    if parsed.locations:
        preferences["TARGET_LOCATIONS"] = ", ".join(parsed.locations)
    if len(parsed.work_modes) == 1:
        preferences["WORK_MODE"] = parsed.work_modes[0].upper()
    tracked_fingerprint = hashlib.sha256(
        "\x1f".join(
            sorted(
                [f"public:{key}:{value}" for key, value in public_tracked.items()]
                + [f"waterloo:{key}:{value}" for key, value in external_tracked.items()]
            )
        ).encode("utf-8")
    ).hexdigest()
    embedding_config = (
        deps.embedding_config or EmbeddingConfig.from_env()
        if parsed.use_semantic_retrieval
        else None
    )
    cache_key = build_cache_key(
        profile_updated_at=profile.updated_at.isoformat(),
        tracked_fingerprint=tracked_fingerprint,
        preferences=preferences,
        library_version=library_version,
        limit=parsed.limit,
        source=parsed.source,
        request_filters={
            "ranking_version": RECOMMENDATION_RANKING_VERSION,
            "target_roles": tuple(parsed.target_roles),
            "locations": tuple(parsed.locations),
            "work_modes": tuple(parsed.work_modes),
            "opportunity_types": tuple(parsed.opportunity_types),
        },
        embedding_profile=(embedding_config.version if embedding_config else "lexical"),
        llm_profile=(
            f"{deps.llm_config.provider}:{deps.llm_config.model_name}"
            if deps.llm_config is not None and parsed.use_llm_rerank
            else "no-rerank"
        ),
        use_semantic_retrieval=parsed.use_semantic_retrieval,
        use_llm_rerank=parsed.use_llm_rerank,
        exclude_tracked=parsed.exclude_tracked,
    )
    cached = recommendation_cache.get(cache_key)
    if cached is not None:
        cached["data"]["cache_hit"] = True
        cached["data"]["timings_ms"] = {
            "total": round((time.perf_counter() - started) * 1000, 1)
        }
        return cached

    recall_started = time.perf_counter()
    used: list[str] = ["profile"]
    candidate_limit = max(120, parsed.limit * 12)
    candidates: list[dict[str, Any]] = []
    retrieval_mode = "recent_fallback"
    requested_sources = (
        ["public", "waterloo_work"] if parsed.source == "all" else [parsed.source]
    )
    retrieval_modes = {source: "recent_fallback" for source in requested_sources}
    retrieval_filters = RecommendationFilters(
        target_roles=expand_target_roles(parsed.target_roles),
        locations=tuple(parsed.locations),
        work_modes=tuple(parsed.work_modes),
        opportunity_types=tuple(parsed.opportunity_types),
    )
    query_text = build_profile_query(profile, preferences)
    embedding: list[float] | None = None
    embedding_ready = {source: False for source in requested_sources}
    if deps.recommendation_repo is not None and parsed.use_semantic_retrieval:
        try:
            if embedding_config is not None:
                readiness = await asyncio.gather(
                    *(
                        deps.recommendation_repo.has_embeddings(
                            embedding_config, source=source
                        )
                        for source in requested_sources
                    )
                )
                embedding_ready.update(dict(zip(requested_sources, readiness, strict=True)))
            if embedding_config is not None and any(embedding_ready.values()):
                embedding = await asyncio.to_thread(
                    EmbeddingGateway(embedding_config).embed_query, query_text
                )
        except Exception:
            embedding = None

    if parsed.source in {"all", "public"}:
        exclude_ids = []
        if parsed.exclude_tracked:
            for value in public_tracked:
                try:
                    exclude_ids.append(UUID(value))
                except (TypeError, ValueError):
                    continue
        rows: list[dict[str, Any]] = []
        if deps.recommendation_repo is not None:
            try:
                rows = await deps.recommendation_repo.recall_public(
                    query_text=query_text,
                    skills=sorted(skills),
                    exclude_public_ids=exclude_ids,
                    query_embedding=embedding if embedding_ready.get("public") else None,
                    embedding_config=(
                        embedding_config if embedding_ready.get("public") else None
                    ),
                    limit=candidate_limit,
                    filters=retrieval_filters,
                )
                if rows:
                    retrieval_modes["public"] = (
                        "hybrid_vector_lexical"
                        if embedding_ready.get("public")
                        else "hybrid_lexical"
                    )
            except Exception:
                rows = []
        recall_method = getattr(deps.job_repo, "list_jobs_for_recommendation", None)
        if not rows and recall_method is not None:
            fallback_args: dict[str, Any] = {
                "skills": sorted(skills),
                "exclude_public_ids": exclude_ids,
                "limit": candidate_limit,
            }
            if isinstance(deps.job_repo, JobReadRepository):
                fallback_args.update(
                    target_roles=list(retrieval_filters.target_roles),
                    locations=list(retrieval_filters.locations),
                    work_modes=list(retrieval_filters.work_modes),
                    opportunity_types=list(retrieval_filters.opportunity_types),
                )
            rows = await recall_method(
                **fallback_args,
            )
            retrieval_modes["public"] = "skill_fulltext_fallback"
        if rows:
            for row in rows:
                item: JobListItem = row["item"]
                location = item.location.display_name if item.location else None
                candidates.append(
                    {
                        "source": "public",
                        "job_id": str(item.id),
                        "title": item.title,
                        "company": item.company_name,
                        "location": location,
                        "location_text": location,
                        "work_mode": item.work_mode,
                        "opportunity_type": item.opportunity_type,
                        "recruiting_term": (
                            item.recruiting_term.display_name
                            if item.recruiting_term
                            else None
                        ),
                        "date_posted": (
                            item.date_posted.isoformat() if item.date_posted else None
                        ),
                        "application_deadline": None,
                        "application_url": row.get("url"),
                        "skill_tags": item.skill_tags + item.display_tags,
                        "requirement_tags": row.get("requirement_tags") or [],
                        "description": row.get("description"),
                        "retrieval": row.get("retrieval") or {},
                        "retrieval_sources": row.get("retrieval_sources")
                        or ["skill_tags", "full_text"],
                    }
                )
        else:  # Compatibility for lightweight repository implementations.
            page = await deps.job_repo.list_jobs(JobListFilters(limit=100))
            details = await asyncio.gather(
                *(deps.job_repo.get_job(item.id) for item in page.items),
                return_exceptions=True,
            )
            for item, detail in zip(page.items, details, strict=True):
                if parsed.exclude_tracked and str(item.id) in public_tracked:
                    continue
                valid_detail = detail if isinstance(detail, JobDetail) else None
                sources = getattr(valid_detail, "sources", None) or []
                location = item.location.display_name if item.location else None
                candidates.append(
                    {
                        "source": "public",
                        "job_id": str(item.id),
                        "title": item.title,
                        "company": item.company_name,
                        "location": location,
                        "location_text": location,
                        "work_mode": item.work_mode,
                        "date_posted": (
                            item.date_posted.isoformat() if item.date_posted else None
                        ),
                        "application_deadline": None,
                        "application_url": (
                            (sources[0].direct_url or sources[0].url) if sources else None
                        ),
                        "skill_tags": item.skill_tags + item.display_tags,
                        "requirement_tags": (
                            getattr(valid_detail, "requirement_tags", None) or []
                        ),
                        "description": (
                            valid_detail.description if valid_detail is not None else None
                        ),
                        "retrieval_sources": ["recent_fallback"],
                    }
                )
        used.append("public_jobs")

    if parsed.source in {"all", "waterloo_work"}:
        waterloo_rows: list[dict[str, Any]] = []
        if deps.recommendation_repo is not None:
            try:
                waterloo_rows = await deps.recommendation_repo.recall_waterloo(
                    query_text=query_text,
                    skills=sorted(skills),
                    exclude_external_ids=(
                        list(external_tracked) if parsed.exclude_tracked else []
                    ),
                    query_embedding=(
                        embedding if embedding_ready.get("waterloo_work") else None
                    ),
                    embedding_config=(
                        embedding_config
                        if embedding_ready.get("waterloo_work")
                        else None
                    ),
                    limit=min(candidate_limit, 200),
                    filters=retrieval_filters,
                )
                if waterloo_rows:
                    retrieval_modes["waterloo_work"] = (
                        "hybrid_vector_lexical"
                        if embedding_ready.get("waterloo_work")
                        else "hybrid_lexical"
                    )
            except Exception:
                waterloo_rows = []
        if not waterloo_rows:
            ww = await deps.waterlooworks.list_jobs(
                work_modes=parsed.work_modes,
                opportunity_types=parsed.opportunity_types,
                limit=min(max(candidate_limit * 5, 1000), 5000),
                include_description=True,
            )
            waterloo_rows = ww["items"]
            retrieval_modes["waterloo_work"] = "waterloo_recent_fallback"
        for item in waterloo_rows:
            job_id = item.get("source_job_id")
            if parsed.exclude_tracked and job_id in external_tracked:
                continue
            location = clean_location_display(item.get("location_text"))
            candidates.append(
                {
                    "source": "waterloo_work",
                    "job_id": job_id,
                    "title": item.get("title"),
                    "company": item.get("organization"),
                    "location": location,
                    "location_text": location,
                    "work_mode": item.get("work_mode"),
                    "opportunity_type": item.get("opportunity_type")
                    or infer_waterloo_opportunity_type(item.get("boards")),
                    "date_posted": item.get("date_posted"),
                    "application_deadline": item.get("application_deadline"),
                    "application_url": item.get("application_url"),
                    "skill_tags": item.get("skill_tags")
                    or [item.get("division") or ""],
                    "requirement_tags": [],
                    "description": item.get("description"),
                    "retrieval": item.get("retrieval") or {},
                    "retrieval_sources": item.get("retrieval_sources")
                    or ["waterloo_recent"],
                }
            )
        used.append("waterloo_work")
    candidates = _cap_candidates_by_source(candidates, limit=candidate_limit)
    unique_modes = set(retrieval_modes.values())
    retrieval_mode = (
        next(iter(unique_modes)) if len(unique_modes) == 1 else "mixed"
    )
    recall_ms = (time.perf_counter() - recall_started) * 1000

    rank_started = time.perf_counter()
    ranked: list[dict[str, Any]] = []
    early_career = _is_early_career_profile(profile)
    for candidate in candidates:
        if not candidate.get("job_id") or is_expired(candidate):
            continue
        location_text = normalize_for_matching(candidate.get("location") or "")
        if parsed.target_roles and not target_role_matches(
            candidate.get("title"), parsed.target_roles
        ):
            continue
        if parsed.locations and not any(
            normalize_for_matching(location) in location_text
            for location in parsed.locations
        ):
            continue
        if parsed.work_modes and candidate.get("work_mode") not in parsed.work_modes:
            continue
        if parsed.opportunity_types and candidate.get("opportunity_type") not in set(
            parsed.opportunity_types
        ):
            continue
        scored: ScoredCandidate = score_candidate(
            skills,
            candidate,
            preferences=preferences,
            early_career=early_career,
            desired_opportunity_types=set(parsed.opportunity_types),
        )
        pref_matches = _preference_matches(preferences, candidate)
        ranked.append(
            {
                **candidate,
                "score": scored.score,
                "matched_skills": scored.matched_skills[:12],
                "signals": scored.signals,
                "preference_matches": pref_matches,
                "description_available": bool(candidate.get("description")),
            }
        )
    ranked.sort(
        key=lambda candidate: (
            candidate["score"],
            candidate.get("date_posted") or "",
        ),
        reverse=True,
    )

    llm_rerank_ms = 0.0
    llm_rerank_status = (
        "disabled"
        if not parsed.use_llm_rerank
        else "unconfigured"
        if deps.llm_config is None
        else "skipped"
    )
    llm_rerank_error_type: str | None = None
    if parsed.use_llm_rerank and deps.llm_config is not None and len(ranked) > 1:
        llm_started = time.perf_counter()
        outcome = await asyncio.to_thread(
            rerank_with_llm,
            llm_config=deps.llm_config,
            candidates=ranked,
            profile_summary={
                "skills": sorted(skills)[:80],
                "education_stage": _education_stage(profile),
                "target_roles": preferences.get("TARGET_ROLES", ""),
            },
            preferences=preferences,
            language=preferences.get("ANSWER_LANGUAGE", "the user's language"),
        )
        llm_rerank_ms = (time.perf_counter() - llm_started) * 1000
        llm_rerank_status = outcome.status
        llm_rerank_error_type = outcome.error_type
        if outcome.adjustments:
            for index, adjustment in outcome.adjustments.items():
                ranked[index]["llm_adjustment"] = adjustment
                ranked[index]["llm_reason"] = outcome.reasons.get(index)
            ranked = sorted(
                enumerate(ranked),
                key=lambda pair: (
                    pair[1]["score"] + pair[1].get("llm_adjustment", 0),
                    -pair[0],
                ),
                reverse=True,
            )
            ranked = [candidate for _, candidate in ranked]

    selected = enforce_company_diversity(ranked, limit=parsed.limit)
    recommendations: list[dict[str, Any]] = []
    for candidate in selected:
        matched = candidate["matched_skills"]
        reasons = []
        if matched:
            reasons.append("Matches profile skills: " + ", ".join(matched))
        else:
            reasons.append(
                "Semantic or role relevance found, but direct skill evidence is limited."
            )
        for match in candidate.get("preference_matches") or []:
            reasons.append(f"Matches your stated preference: {match}")
        if candidate.get("llm_reason"):
            reasons.append(candidate["llm_reason"])
        if not candidate["description_available"]:
            reasons.append("Job description is incomplete; confidence is reduced.")
        score = candidate["score"] + candidate.get("llm_adjustment", 0)
        if score >= 60 and len(matched) >= 3:
            confidence = "high"
        elif score >= 30 or matched:
            confidence = "medium"
        else:
            confidence = "low"
        public_candidate = {
            key: value
            for key, value in candidate.items()
            if key != "description"
        }
        recommendations.append(
            {
                **public_candidate,
                "match_score": max(0, min(100, score)),
                "confidence": confidence,
                "matched_signals": [
                    {"signal": name, "value": value}
                    for name, value in (
                        candidate.get("signals", {}).get("components", {})
                    ).items()
                    if value > 0
                ],
                "gaps": [
                    {"signal": "requirement", "value": value}
                    for value in candidate.get("signals", {}).get(
                        "unmatched_requirement_tags", []
                    )
                ]
                + [
                    {"signal": "penalty", "value": name, "weight": value}
                    for name, value in candidate.get("signals", {}).get(
                        "penalties", {}
                    ).items()
                ],
                "unknowns": [
                    {"signal": value}
                    for value, unknown in (
                        ("job_description", not candidate["description_available"]),
                        (
                            "application_deadline",
                            not candidate.get("application_deadline"),
                        ),
                    )
                    if unknown
                ],
                "reasons": reasons,
            }
        )
    rank_ms = (time.perf_counter() - rank_started) * 1000
    result = {
        "ok": True,
        "data": {
            "recommendations": recommendations,
            "profile_used": {
                "skills": sorted(skills)[:50],
                "completion_percent": profile.completion_percent,
                "early_career": early_career,
            },
            "retrieval_mode": retrieval_mode,
            "retrieval_modes": retrieval_modes,
            "llm_rerank": {
                "status": llm_rerank_status,
                "applied": llm_rerank_status == "applied",
                "error_type": llm_rerank_error_type,
            },
            "corpus_version": library_version,
            "ranking_version": RECOMMENDATION_RANKING_VERSION,
            "cache_hit": False,
            "candidate_counts": {
                "recalled": len(candidates),
                "eligible": len(ranked),
                "returned": len(recommendations),
            },
            "timings_ms": {
                "recall": round(recall_ms, 1),
                "rank_and_rerank": round(rank_ms, 1),
                "llm_rerank": round(llm_rerank_ms, 1),
                "total": round((time.perf_counter() - started) * 1000, 1),
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
    recommendation_cache.put(cache_key, result)
    return result


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
                    job_url=None,
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


async def tool_generate_interview_questions(
    args: dict[str, Any], deps: AgentDeps, phase: str
) -> dict[str, Any]:
    """Generate mock interview questions for one job or a raw description."""

    from wecanfindintern.interview.service import (
        build_resume_text,
        generate_interview_questions,
    )

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
    resume_text = build_resume_text(profile)
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
            "Recommend jobs with hybrid RAG recall over Profile, job descriptions and "
            "preferences, followed by deterministic evidence scoring and an optional "
            "bounded LLM review. Never writes user data."
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
        "name": "generate_interview_questions",
        "description": (
            "Generate mock interview questions for one job. Resolve the job with "
            "search_jobs/get_job_details first and pass job_id plus source, or pass a "
            "raw job_description. Read-only."
        ),
        "parameters": GenerateInterviewQuestionsArgs.model_json_schema(),
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
