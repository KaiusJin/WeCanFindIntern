"""Deterministic multi-signal scoring for job recommendations.

Pure functions only: the same profile skills and candidate always produce the
same score and signal breakdown, which keeps ranking testable and explainable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from wecanfindintern.domain.classification import normalize_for_matching
from wecanfindintern.domain.normalization import normalize_company, normalize_title

# Component caps keep one long skill list or description from dominating the
# ranking. The score is relative fit evidence, never an admission probability.
MAX_SKILL_TAG_SCORE = 30
MAX_TITLE_SCORE = 20
MAX_REQUIREMENT_SCORE = 20
MAX_DESCRIPTION_SCORE = 10
MAX_SEMANTIC_SCORE = 12
MAX_ROLE_PREFERENCE_SCORE = 12
MAX_LOCATION_PREFERENCE_SCORE = 6
MAX_WORK_MODE_PREFERENCE_SCORE = 5
WEIGHT_OPPORTUNITY_FIT = 12
WEIGHT_EARLY_CAREER_ROLE = 5
PENALTY_SENIOR_ROLE = 25
WEIGHT_FRESH_7D = 5
WEIGHT_FRESH_30D = 2
WEIGHT_DEADLINE_SOON = 3

FRESH_WINDOW_DAYS = 7
FRESH_RECENT_DAYS = 30
DEADLINE_URGENT_DAYS = 14

# Small curated alias table (lowercase, normalize_for_matching form). Used in
# both directions so either side of a pair matches the other.
SKILL_ALIASES: dict[str, tuple[str, ...]] = {
    "js": ("javascript",),
    "javascript": ("js",),
    "ts": ("typescript",),
    "typescript": ("ts",),
    "py": ("python",),
    "ml": ("machine learning",),
    "machine learning": ("ml",),
    "nlp": ("natural language processing",),
    "ai": ("artificial intelligence",),
    "golang": ("go",),
    "go": ("golang",),
    "react.js": ("react",),
    "reactjs": ("react",),
    "node": ("node.js", "nodejs"),
    "node.js": ("node", "nodejs"),
    "nodejs": ("node", "node.js"),
    "postgres": ("postgresql",),
    "postgresql": ("postgres",),
    "k8s": ("kubernetes",),
    "kubernetes": ("k8s",),
    "aws": ("amazon web services",),
    "llm": ("large language models",),
}

ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "software engineer": ("software developer",),
    "machine learning": (
        "artificial intelligence",
        "ai engineer",
        "ml engineer",
        "data scientist",
    ),
    "frontend": ("front end", "front-end", "ui engineer", "web developer"),
    "backend": ("back end", "back-end", "server engineer"),
    "devops": ("site reliability", "platform engineer", "cloud engineer"),
    "data analyst": ("business intelligence analyst", "analytics analyst"),
}

SENIOR_TITLE_PATTERN = re.compile(
    r"\b(?:senior|sr\.?|staff|principal|lead|manager|director|architect|head)\b",
    re.IGNORECASE,
)
EARLY_CAREER_TITLE_PATTERN = re.compile(
    r"\b(?:intern(?:ship)?|co[ -]?op|junior|entry[ -]level|new grad(?:uate)?)\b",
    re.IGNORECASE,
)

_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


def expand_target_roles(roles: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Expand a bounded role phrase list without turning it into loose keywords."""

    expanded: list[str] = []
    for value in roles:
        normalized = normalize_for_matching(value)
        if not normalized:
            continue
        for phrase in (normalized, *ROLE_ALIASES.get(normalized, ())):
            if phrase not in expanded:
                expanded.append(phrase)
    return tuple(expanded)


def target_role_matches(title: str | None, roles: list[str] | tuple[str, ...]) -> bool:
    title_text = normalize_for_matching(title or "")
    return any(phrase in title_text for phrase in expand_target_roles(roles))


def expand_skill_terms(skills: set[str]) -> set[str]:
    """Lowercase matching forms of profile skills, with aliases expanded."""

    terms: set[str] = set()
    for skill in skills:
        base = normalize_for_matching(skill)
        if not base:
            continue
        terms.add(base)
        terms.update(SKILL_ALIASES.get(base, ()))
    return {term for term in terms if term}


def expand_skill_tags(tags: list[str]) -> set[str]:
    """Normalized tag space of a job, also alias-expanded."""

    expanded: set[str] = set()
    for tag in tags:
        base = normalize_for_matching(tag.replace("_", " "))
        if not base:
            continue
        expanded.add(base)
        expanded.update(SKILL_ALIASES.get(base, ()))
    return expanded


def _term_pattern(term: str) -> re.Pattern[str]:
    pattern = _PATTERN_CACHE.get(term)
    if pattern is None:
        if re.search(r"[^\x00-\x7f]", term):
            pattern = re.compile(re.escape(term), re.IGNORECASE)
        else:
            # Custom boundaries so terms like "c++", "c#", ".net" still match
            # as whole terms while "go" never matches inside "google".
            pattern = re.compile(
                rf"(?<![\w#+.]){re.escape(term)}(?![\w#+.])", re.IGNORECASE
            )
        _PATTERN_CACHE[term] = pattern
    return pattern


def _match_terms(terms: set[str], text: str | None) -> list[str]:
    if not text or not terms:
        return []
    matched = [term for term in sorted(terms) if _term_pattern(term).search(text)]
    return matched


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None


@dataclass(slots=True)
class ScoredCandidate:
    score: int
    matched_skills: list[str]
    signals: dict[str, Any] = field(default_factory=dict)


def score_candidate(
    profile_skills: set[str],
    candidate: dict[str, Any],
    *,
    preferences: dict[str, str] | None = None,
    early_career: bool = False,
    desired_opportunity_types: set[str] | None = None,
    today: date | None = None,
) -> ScoredCandidate:
    """Score one candidate using bounded, inspectable fit components."""

    today = today or datetime.now(UTC).date()
    terms = expand_skill_terms(profile_skills)
    job_tag_terms = expand_skill_tags(candidate.get("skill_tags") or [])

    tag_overlap = sorted(terms & job_tag_terms)
    title_matches = _match_terms(terms, candidate.get("title") or "")
    requirement_matches = _match_terms(
        terms, " ".join(candidate.get("requirement_tags") or [])
    )
    description_matches = _match_terms(terms, candidate.get("description") or "")

    components: dict[str, int] = {
        "skill_tags": min(MAX_SKILL_TAG_SCORE, len(tag_overlap) * 6),
        "title_alignment": min(MAX_TITLE_SCORE, len(title_matches) * 10),
        "requirements": min(MAX_REQUIREMENT_SCORE, len(requirement_matches) * 5),
        "description": min(MAX_DESCRIPTION_SCORE, len(description_matches) * 2),
    }

    retrieval = candidate.get("retrieval") or {}
    semantic_score = retrieval.get("semantic_score")
    if isinstance(semantic_score, (int, float)) and not isinstance(semantic_score, bool):
        components["semantic"] = round(
            MAX_SEMANTIC_SCORE * max(0.0, min(1.0, float(semantic_score)))
        )

    preferences = preferences or {}
    title_text = normalize_for_matching(candidate.get("title") or "")
    location_text = normalize_for_matching(
        candidate.get("location_text") or candidate.get("location") or ""
    )
    target_roles = expand_target_roles(preferences.get("TARGET_ROLES", "").split(","))
    if any(role in title_text for role in target_roles):
        components["role_preference"] = MAX_ROLE_PREFERENCE_SCORE
    target_locations = [
        normalize_for_matching(item)
        for item in preferences.get("TARGET_LOCATIONS", "").split(",")
        if normalize_for_matching(item)
    ]
    if any(location in location_text for location in target_locations):
        components["location_preference"] = MAX_LOCATION_PREFERENCE_SCORE
    preferred_mode = preferences.get("WORK_MODE", "").strip().lower()
    actual_mode = (candidate.get("work_mode") or "").strip().lower()
    if preferred_mode and preferred_mode != "any" and (
        actual_mode == preferred_mode
        or (preferred_mode == "onsite" and actual_mode in {"onsite", "in_person"})
    ):
        components["work_mode_preference"] = MAX_WORK_MODE_PREFERENCE_SCORE

    opportunity_type = (candidate.get("opportunity_type") or "").strip().lower()
    title = candidate.get("title") or ""
    is_early_career_opportunity = opportunity_type in {
        "internship",
        "co_op",
        "new_grad",
        "apprenticeship",
    } or bool(
        re.search(r"\b(?:intern(?:ship)?|co[ -]?op)\b", title, re.IGNORECASE)
    )
    desired = {value.strip().lower() for value in desired_opportunity_types or set()}
    if (desired and opportunity_type in desired) or (
        early_career and is_early_career_opportunity
    ):
        components["opportunity_fit"] = WEIGHT_OPPORTUNITY_FIT
    if early_career and EARLY_CAREER_TITLE_PATTERN.search(title):
        components["early_career_role"] = WEIGHT_EARLY_CAREER_ROLE

    penalties: dict[str, int] = {}
    if early_career and SENIOR_TITLE_PATTERN.search(title):
        penalties["senior_role"] = PENALTY_SENIOR_ROLE

    score = sum(components.values()) - sum(penalties.values())

    matched = sorted(set(tag_overlap) | set(title_matches) | set(requirement_matches))

    freshness = None
    posted = _parse_date(candidate.get("date_posted"))
    if posted is not None:
        age_days = (today - posted).days
        if 0 <= age_days <= FRESH_WINDOW_DAYS:
            freshness = "new_this_week"
            score += WEIGHT_FRESH_7D
        elif 0 <= age_days <= FRESH_RECENT_DAYS:
            freshness = "recent"
            score += WEIGHT_FRESH_30D

    deadline_urgent = False
    deadline = _parse_date(
        candidate.get("application_deadline_date")
        or candidate.get("application_deadline")
    )
    if deadline is not None and 0 <= (deadline - today).days <= DEADLINE_URGENT_DAYS:
        deadline_urgent = True
        score += WEIGHT_DEADLINE_SOON

    signals: dict[str, Any] = {
        "components": components,
        "skill_tag_matches": tag_overlap,
        "title_matches": title_matches,
        "requirement_matches": requirement_matches,
        "description_matches": description_matches[:10],
        "unmatched_requirement_tags": sorted(
            expand_skill_tags(candidate.get("requirement_tags") or []) - terms
        )[:10],
        "penalties": penalties,
    }
    if freshness:
        signals["freshness"] = freshness
        signals["components"]["freshness"] = (
            WEIGHT_FRESH_7D if freshness == "new_this_week" else WEIGHT_FRESH_30D
        )
    if deadline_urgent:
        signals["deadline"] = "closing_soon"

    # Ensure the public score stays a bounded relative signal even when future
    # components are added.
    score = max(0, min(100, score))

    return ScoredCandidate(score=score, matched_skills=matched, signals=signals)


def is_expired(candidate: dict[str, Any], *, today: date | None = None) -> bool:
    today = today or datetime.now(UTC).date()
    deadline = _parse_date(
        candidate.get("application_deadline_date")
        or candidate.get("application_deadline")
    )
    return deadline is not None and deadline < today


def enforce_company_diversity(
    ranked: list[dict[str, Any]],
    *,
    limit: int,
    max_per_company: int = 3,
) -> list[dict[str, Any]]:
    """Cap results per company, backfilling from the overflow in rank order."""

    selected: list[dict[str, Any]] = []
    overflow: list[dict[str, Any]] = []
    per_company: dict[str, int] = {}
    seen_jobs: set[tuple[str, str]] = set()
    for candidate in ranked:
        raw_company = candidate.get("company") or ""
        company = normalize_company(raw_company.split("|", 1)[0])
        title_key = normalize_title(candidate.get("title") or "")
        identity = str(candidate.get("job_id") or id(candidate))
        dedupe_key = (
            company or f"unknown:{identity}",
            title_key or f"job:{identity}",
        )
        if dedupe_key in seen_jobs:
            continue
        seen_jobs.add(dedupe_key)
        company_bucket = company or "unknown"
        if per_company.get(company_bucket, 0) < max_per_company:
            per_company[company_bucket] = per_company.get(company_bucket, 0) + 1
            selected.append(candidate)
        else:
            overflow.append(candidate)
        if len(selected) >= limit:
            break
    for candidate in overflow:
        if len(selected) >= limit:
            break
        selected.append(candidate)
    return selected[:limit]
