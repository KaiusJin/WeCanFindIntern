"""Job recommendation Agent tool and its local ranking helpers."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from typing import Any
from uuid import UUID

from wecanfindintern.agent.contracts import AgentDeps
from wecanfindintern.agent.job_access import (
    tracked_external_map,
    tracked_public_map,
)
from wecanfindintern.agent.models import RecommendJobsArgs
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
from wecanfindintern.agent.recommend.documents import build_profile_query
from wecanfindintern.agent.recommend.embeddings import EmbeddingConfig, EmbeddingGateway
from wecanfindintern.agent.recommend.repository import RecommendationFilters
from wecanfindintern.agent.recommend.scoring import ScoredCandidate
from wecanfindintern.application.job_models import JobDetail, JobListFilters, JobListItem
from wecanfindintern.db.read_repository import JobReadRepository
from wecanfindintern.domain.classification import normalize_for_matching
from wecanfindintern.domain.location import clean_location_display
from wecanfindintern.profile.models import UserProfile
from wecanfindintern.waterlooworks.taxonomy import resolve_waterloo_opportunity_type

RECOMMENDATION_RANKING_VERSION = "recommend.v4"


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
    graduation = latest.graduation_date_text or latest.graduation_year
    if graduation:
        prefix = "expected " if latest.expected_graduation else ""
        parts.append(f"{prefix}graduation {graduation}")
    return ", ".join(part for part in parts if part)


def _is_early_career_profile(profile: UserProfile) -> bool:
    if any(entry.expected_graduation or entry.status == "studying" for entry in profile.education):
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


def _preference_matches(preferences: dict[str, str], candidate: dict[str, Any]) -> list[str]:
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


async def tool_recommend_jobs(args: dict[str, Any], deps: AgentDeps, phase: str) -> dict[str, Any]:
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

    profile, preferences, public_tracked, external_tracked, library_version = await asyncio.gather(
        deps.profile_repo.get_profile(),
        load_preferences(),
        tracked_public_map(deps),
        tracked_external_map(deps),
        load_library_version(),
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
        cached["data"]["timings_ms"] = {"total": round((time.perf_counter() - started) * 1000, 1)}
        return cached

    recall_started = time.perf_counter()
    used: list[str] = ["profile"]
    candidate_limit = max(120, parsed.limit * 12)
    candidates: list[dict[str, Any]] = []
    retrieval_mode = "recent_fallback"
    requested_sources = ["public", "waterloo_work"] if parsed.source == "all" else [parsed.source]
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
                        deps.recommendation_repo.has_embeddings(embedding_config, source=source)
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
                    embedding_config=(embedding_config if embedding_ready.get("public") else None),
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
                            item.recruiting_term.display_name if item.recruiting_term else None
                        ),
                        "date_posted": (item.date_posted.isoformat() if item.date_posted else None),
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
                        "date_posted": (item.date_posted.isoformat() if item.date_posted else None),
                        "application_deadline": None,
                        "application_url": (
                            (sources[0].direct_url or sources[0].url) if sources else None
                        ),
                        "skill_tags": item.skill_tags + item.display_tags,
                        "requirement_tags": (getattr(valid_detail, "requirement_tags", None) or []),
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
                    exclude_external_ids=(list(external_tracked) if parsed.exclude_tracked else []),
                    query_embedding=(embedding if embedding_ready.get("waterloo_work") else None),
                    embedding_config=(
                        embedding_config if embedding_ready.get("waterloo_work") else None
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
                    "opportunity_type": resolve_waterloo_opportunity_type(
                        item.get("opportunity_type"), item.get("boards")
                    ),
                    "date_posted": item.get("date_posted"),
                    "application_deadline": item.get("application_deadline"),
                    "application_url": item.get("application_url") or item.get("source_url"),
                    "skill_tags": item.get("skill_tags") or [item.get("division") or ""],
                    "requirement_tags": item.get("requirement_tags") or [],
                    "description": item.get("description"),
                    "retrieval": item.get("retrieval") or {},
                    "retrieval_sources": item.get("retrieval_sources") or ["waterloo_recent"],
                }
            )
        used.append("waterloo_work")
    candidates = _cap_candidates_by_source(candidates, limit=candidate_limit)
    unique_modes = set(retrieval_modes.values())
    retrieval_mode = next(iter(unique_modes)) if len(unique_modes) == 1 else "mixed"
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
            normalize_for_matching(location) in location_text for location in parsed.locations
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
        public_candidate = {key: value for key, value in candidate.items() if key != "description"}
        recommendation = {
            **public_candidate,
            "match_score": max(0, min(100, score)),
            "confidence": confidence,
            "matched_signals": [
                {"signal": name, "value": value}
                for name, value in (candidate.get("signals", {}).get("components", {})).items()
                if value > 0
            ],
            "gaps": [
                {"signal": "requirement", "value": value}
                for value in candidate.get("signals", {}).get("unmatched_requirement_tags", [])
            ]
            + [
                {"signal": "penalty", "value": name, "weight": value}
                for name, value in candidate.get("signals", {}).get("penalties", {}).items()
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
        recommendations.append(recommendation)
    analysis_targets = [
        {"job_id": recommendation["job_id"], "source": recommendation["source"]}
        for recommendation in recommendations[:2]
    ]
    rank_ms = (time.perf_counter() - rank_started) * 1000
    result = {
        "ok": True,
        "data": {
            "recommendations": recommendations,
            "analysis_targets": analysis_targets,
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
                "analysis_targets": len(analysis_targets),
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
            f"Recommended {len(recommendations)} job(s) based on {len(skills)} profile skills."
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
