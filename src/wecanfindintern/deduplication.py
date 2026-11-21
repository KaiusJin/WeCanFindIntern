"""Match source records within a collection round and against indexed database candidates."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Any

from wecanfindintern.domain.jobs import CanonicalJobInput
from wecanfindintern.domain.salary import has_salary_period


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
    source_identities: frozenset[tuple[str, str | None]] = frozenset()


@dataclass(frozen=True, slots=True)
class DedupeDecision:
    action: DedupeAction
    candidate_job_id: int | None
    score: float
    evidence: dict[str, float | bool | int | None]


def record_completeness(record: Mapping[str, Any]) -> tuple[int, int, int]:
    """Compare populated posting fields, salary detail, then the amount of JD text."""
    fields = (
        "title",
        "company",
        "location",
        "date_posted",
        "job_type",
        "job_function",
        "job_url_direct",
        "company_industry",
        "company_url_direct",
        "company_logo",
        "skills",
        "emails",
        "vacancy_count",
    )

    def filled(value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().casefold() not in {"", "n/a", "unknown"}
        if isinstance(value, bool):
            return value
        return value is not None and value != [] and value != ()

    label = record.get("salary_label") or ""
    amount = record.get("min_amount") is not None or record.get("max_amount") is not None
    pay = int(amount or bool(re.search(r"\d", label)))
    if pay and (record.get("interval") or has_salary_period(label)):
        pay += 1
    length = record.get("description_length")
    if length is None:
        length = len((record.get("description") or "").strip())
    coverage = sum(filled(record.get(key)) for key in fields) + bool(length) + bool(pay)
    return coverage, pay, length


def group_round_jobs(jobs: Iterable[CanonicalJobInput]) -> list[list[CanonicalJobInput]]:
    """Match within sites, then across sites. Keep each group's distinct source records."""
    unique: dict[tuple[str, str], CanonicalJobInput] = {}
    for job in jobs:
        key = (job.source.source, job.source.source_job_id or job.source.source_fingerprint)
        previous = unique.get(key)
        if previous is None or record_completeness(job.source.payload) > record_completeness(
            previous.source.payload
        ):
            unique[key] = job
    records = [unique[key] for key in sorted(unique)]
    parents = list(range(len(records)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    candidates: list[CandidateJob] = []
    by_key: dict[tuple[str, str, bool], list[int]] = defaultdict(list)
    sites = sorted({job.source.source for job in records})
    for i, job in enumerate(records):
        direct = job.dedupe.direct_url_hash
        site, source_id = job.source.source, job.source.source_job_id
        candidates.append(
            CandidateJob(
                i,
                job.title_normalized,
                job.company.normalized_name,
                job.location.normalized,
                job.work_mode.value,
                job.date_posted,
                job.description,
                job.dedupe.description_hash,
                frozenset([direct]) if direct else frozenset(),
                frozenset([(site, source_id)]),
            )
        )
        for key in (job.dedupe.block_key, direct):
            if key:
                by_key[site, key, False].append(i)
                if site == "linkedin" and not source_id:
                    by_key[site, key, True].append(i)

    for cross_site in (False, True):
        for i, job in enumerate(records):
            for site in sites:
                if (site != job.source.source) != cross_site:
                    continue
                # Two known LinkedIn IDs have already had their only comparison, by ID.
                only_idless = (
                    not cross_site and site == "linkedin" and bool(job.source.source_job_id)
                )
                possible = set(by_key.get((site, job.dedupe.block_key, only_idless), ()))
                if job.dedupe.direct_url_hash:
                    possible.update(by_key.get((site, job.dedupe.direct_url_hash, only_idless), ()))
                for j in possible:
                    if j >= i or root(i) == root(j):
                        continue
                    other = records[j]
                    if (
                        job.date_posted
                        and other.date_posted
                        and abs((job.date_posted - other.date_posted).days) > 60
                        and not (
                            job.dedupe.direct_url_hash
                            and job.dedupe.direct_url_hash == other.dedupe.direct_url_hash
                        )
                    ):
                        continue
                    if score_candidate(job, candidates[j], rank=False).action is DedupeAction.MERGE:
                        parents[root(i)] = root(j)

    groups: dict[int, list[CanonicalJobInput]] = defaultdict(list)
    for i, job in enumerate(records):
        groups[root(i)].append(job)
    for members in groups.values():
        members.sort(key=lambda job: record_completeness(job.source.payload), reverse=True)
    return list(groups.values())


def choose_duplicate(
    incoming: CanonicalJobInput,
    candidates: Iterable[CandidateJob],
) -> DedupeDecision:
    decisions = [score_candidate(incoming, candidate) for candidate in candidates]
    if not decisions:
        return DedupeDecision(DedupeAction.CREATE, None, 0.0, {})

    best = max(decisions, key=lambda item: (item.action is DedupeAction.MERGE, item.score))
    if best.action is DedupeAction.MERGE:
        return best
    return DedupeDecision(
        DedupeAction.CREATE,
        best.candidate_job_id,
        best.score,
        best.evidence,
    )


def score_candidate(
    incoming: CanonicalJobInput, candidate: CandidateJob, *, rank: bool = True
) -> DedupeDecision:
    identity = incoming.source
    if identity.source == "linkedin" and identity.source_job_id and candidate.source_identities:
        if ("linkedin", identity.source_job_id) in candidate.source_identities:
            return DedupeDecision(
                DedupeAction.MERGE, candidate.job_id, 1.0, {"same_linkedin_id": True}
            )
        # An Indeed posting or an ID-less LinkedIn posting still gets a content comparison.
        if all(site == "linkedin" and key for site, key in candidate.source_identities):
            return DedupeDecision(
                DedupeAction.CREATE, candidate.job_id, 0.0, {"same_linkedin_id": False}
            )
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
    content_candidate = (
        company_exact
        and title_similarity >= 0.94
        and location_similarity >= 0.80
        and (days_apart is None or days_apart <= 60)
    )
    description_similarity = float(description_exact)
    if not description_exact and (rank or content_candidate):
        description_similarity = shingle_jaccard(incoming.description, candidate.description)

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
    strong_content_identity = content_candidate and description_similarity >= 0.78
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
            "algorithm_version": 3,
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
