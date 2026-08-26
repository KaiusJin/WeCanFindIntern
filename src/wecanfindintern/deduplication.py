"""Candidate scoring after indexed PostgreSQL blocking.

This module deliberately never searches the whole corpus. The repository first
uses compact exact hashes and company/location/date indexes to produce at most a
small candidate set, then these functions make the merge decision.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from enum import StrEnum

from wecanfindintern.domain.jobs import CanonicalJobInput


class DedupeAction(StrEnum):
    MERGE = "merge"
    CREATE = "create"


@dataclass(frozen=True, slots=True)
class CandidateJob:
    job_id: int
    title_normalized: str
    company_normalized: str
    location_normalized: str
    work_mode: str
    date_posted: date | None
    description: str | None
    description_hash: str | None
    direct_url_hashes: frozenset[str]


@dataclass(frozen=True, slots=True)
class DedupeDecision:
    action: DedupeAction
    candidate_job_id: int | None
    score: float
    evidence: dict[str, float | bool | int | None]


def choose_duplicate(
    incoming: CanonicalJobInput,
    candidates: Iterable[CandidateJob],
) -> DedupeDecision:
    decisions = [score_candidate(incoming, candidate) for candidate in candidates]
    if not decisions:
        return DedupeDecision(DedupeAction.CREATE, None, 0.0, {})

    best = max(decisions, key=lambda item: item.score)
    if best.action is DedupeAction.MERGE:
        return best
    return DedupeDecision(
        DedupeAction.CREATE,
        best.candidate_job_id,
        best.score,
        best.evidence,
    )


def score_candidate(incoming: CanonicalJobInput, candidate: CandidateJob) -> DedupeDecision:
    direct_exact = bool(
        incoming.dedupe.direct_url_hash
        and incoming.dedupe.direct_url_hash in candidate.direct_url_hashes
    )
    company_exact = bool(
        incoming.dedupe.company_key and incoming.dedupe.company_key == candidate.company_normalized
    )
    title_similarity = ratio(incoming.dedupe.title_key, candidate.title_normalized)
    location_similarity = location_score(incoming, candidate)
    date_similarity, days_apart = date_score(incoming.date_posted, candidate.date_posted)
    description_exact = bool(
        incoming.dedupe.description_hash
        and incoming.dedupe.description_hash == candidate.description_hash
    )
    description_similarity = (
        1.0 if description_exact else shingle_jaccard(incoming.description, candidate.description)
    )

    direct_identity = direct_exact and company_exact and title_similarity >= 0.65
    if direct_identity:
        score = 1.0
    else:
        score = (
            0.30 * float(company_exact)
            + 0.35 * title_similarity
            + 0.15 * location_similarity
            + 0.10 * date_similarity
            + 0.10 * description_similarity
        )

    exact_description_identity = description_exact and company_exact and title_similarity >= 0.75
    strong_content_identity = (
        company_exact
        and title_similarity >= 0.94
        and location_similarity >= 0.80
        and description_similarity >= 0.78
        and (days_apart is None or days_apart <= 60)
    )
    exact_listing_identity = (
        company_exact
        and title_similarity >= 0.985
        and location_similarity >= 0.95
        and days_apart is not None
        and days_apart <= 3
    )
    if (
        direct_identity
        or exact_description_identity
        or strong_content_identity
        or exact_listing_identity
    ):
        action = DedupeAction.MERGE
    else:
        action = DedupeAction.CREATE

    return DedupeDecision(
        action=action,
        candidate_job_id=candidate.job_id,
        score=round(score, 4),
        evidence={
            "direct_url_exact": direct_exact,
            "direct_url_identity": direct_identity,
            "company_exact": company_exact,
            "title_similarity": round(title_similarity, 4),
            "location_similarity": round(location_similarity, 4),
            "date_similarity": round(date_similarity, 4),
            "days_apart": days_apart,
            "description_exact": description_exact,
            "description_similarity": round(description_similarity, 4),
            "algorithm_version": 2,
        },
    )


def ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def location_score(incoming: CanonicalJobInput, candidate: CandidateJob) -> float:
    if incoming.work_mode.value == "remote" and candidate.work_mode == "remote":
        return 1.0
    left = incoming.dedupe.location_key
    right = candidate.location_normalized
    if left and right:
        return 1.0 if left == right else ratio(left, right)
    return 0.5


def date_score(left: date | None, right: date | None) -> tuple[float, int | None]:
    if left is None or right is None:
        return 0.5, None
    distance = abs((left - right).days)
    if distance <= 3:
        return 1.0, distance
    if distance <= 14:
        return 0.7, distance
    if distance <= 60:
        return 0.3, distance
    return 0.0, distance


def shingle_jaccard(
    left: str | None,
    right: str | None,
    *,
    width: int = 5,
    token_limit: int = 2_000,
) -> float:
    left_shingles = shingles(left, width=width, token_limit=token_limit)
    right_shingles = shingles(right, width=width, token_limit=token_limit)
    if not left_shingles or not right_shingles:
        return 0.0
    return len(left_shingles & right_shingles) / len(left_shingles | right_shingles)


def shingles(value: str | None, *, width: int, token_limit: int) -> set[tuple[str, ...]]:
    if not value:
        return set()
    tokens = re.findall(r"[\w+#.]{2,}", value.casefold())[:token_limit]
    if len(tokens) < width:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[index : index + width]) for index in range(len(tokens) - width + 1)}
