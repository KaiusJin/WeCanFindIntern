"""Evidence-backed deterministic resume-to-job matching."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

from wecanfindintern.ats.models import JobMatchResult, MatchEvidence, ScoreBreakdown
from wecanfindintern.domain.classification import (
    CATEGORY_RULES,
    SKILL_RULES,
    JobCategory,
    contains_any,
    normalize_for_matching,
)

PREFERRED_MARKERS = (
    "preferred",
    "nice to have",
    "nice-to-have",
    "asset",
    "bonus",
    "ideally",
)
REQUIRED_MARKERS = ("required", "must", "need", "minimum", "qualification")
STOPWORDS = {
    "and", "the", "with", "for", "that", "this", "from", "you", "your", "our",
    "are", "will", "have", "has", "into", "using", "work", "role", "team", "job",
    "years", "experience", "skills", "ability", "including", "about", "who", "but",
    "not", "all", "can", "their", "they", "we", "an", "a", "of", "to", "in", "on",
    "is", "be", "as", "or", "at", "by", "it",
}
TOKEN_PATTERN = re.compile(r"[a-z][a-z0-9+#.]{2,}")
NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "fifteen": 15,
    "twenty": 20,
}
YEARS_PATTERN = re.compile(
    r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|fifteen|twenty)\s*\+?\s*(?:years?|yrs?)\b",
    re.IGNORECASE,
)
DATE_RANGE_PATTERN = re.compile(
    r"\b((?:19|20)\d{2})\s*(?:[-–—]|to)\s*"
    r"((?:19|20)\d{2}|present|current)\b",
    re.IGNORECASE,
)
EDUCATION_RULES: dict[str, tuple[str, ...]] = {
    "Doctorate": ("phd", "ph.d", "doctorate"),
    "Master's degree": (
        "master degree",
        "master's degree",
        "master of",
        "msc",
        "m.sc",
        "mba",
    ),
    "Bachelor's degree": (
        "bachelor degree",
        "bachelor's degree",
        "bachelor of",
        "bsc",
        "b.sc",
        "beng",
    ),
    "College diploma": ("college diploma", "advanced diploma", "diploma"),
}
ELIGIBILITY_PATTERNS = (
    "no sponsorship",
    "unable to sponsor",
    "will not sponsor",
    "security clearance",
    "canadian citizen",
    "citizenship required",
    "driver's license",
    "drivers license",
)


@dataclass(frozen=True, slots=True)
class SkillRequirement:
    name: str
    preferred: bool
    evidence: str


def _line_evidence(text: str, patterns: tuple[str, ...]) -> str | None:
    for line in (line.strip() for line in text.splitlines() if line.strip()):
        normalized = normalize_for_matching(line)
        if contains_any(normalized, patterns):
            return line[:280]
    return None


def _year_values(text: str) -> list[int]:
    values: list[int] = []
    for raw_value in YEARS_PATTERN.findall(text):
        normalized = raw_value.casefold()
        values.append(int(normalized) if normalized.isdigit() else NUMBER_WORDS[normalized])
    return values


def _experience_section(text: str) -> str:
    """Return experience-section text so education dates are not counted as tenure."""

    lines = text.splitlines()
    start: int | None = None
    end_headings = {
        "education",
        "projects",
        "skills",
        "certifications",
        "awards",
        "volunteering",
    }
    for index, line in enumerate(lines):
        heading = normalize_for_matching(line).strip()
        if start is None and heading in {"experience", "work experience", "employment"}:
            start = index + 1
            continue
        if start is not None and heading in end_headings:
            return "\n".join(lines[start:index])
    return "\n".join(lines[start:]) if start is not None else text


def _extract_skill_requirements(job_description: str) -> list[SkillRequirement]:
    requirements: list[SkillRequirement] = []
    for name, aliases in SKILL_RULES.items():
        evidence = _line_evidence(job_description, aliases)
        if evidence is None:
            continue
        normalized_line = normalize_for_matching(evidence)
        preferred = contains_any(normalized_line, PREFERRED_MARKERS) and not contains_any(
            normalized_line, REQUIRED_MARKERS
        )
        requirements.append(
            SkillRequirement(name=name.replace("_", " "), preferred=preferred, evidence=evidence)
        )
    return requirements


def _resume_skill_evidence(resume_text: str, skill_name: str) -> str | None:
    key = skill_name.replace(" ", "_")
    aliases = SKILL_RULES.get(key, (skill_name,))
    return _line_evidence(resume_text, aliases)


def _score_item(
    category: str,
    label: str,
    earned: float,
    maximum: float,
    evidence: list[str],
) -> ScoreBreakdown:
    if maximum == 0:
        status = "unavailable"
    elif earned >= maximum * 0.85:
        status = "pass"
    elif earned >= maximum * 0.5:
        status = "warning"
    else:
        status = "fail"
    return ScoreBreakdown(
        category=category,
        label=label,
        earned=round(earned, 1),
        maximum=maximum,
        status=status,
        evidence=evidence,
    )


def _skill_dimension(
    requirements: list[SkillRequirement],
    resume_text: str,
    *,
    preferred: bool,
    maximum: float,
) -> tuple[ScoreBreakdown, list[MatchEvidence]]:
    selected = [item for item in requirements if item.preferred is preferred]
    evidence_items: list[MatchEvidence] = []
    matched_count = 0
    for requirement in selected:
        resume_evidence = _resume_skill_evidence(resume_text, requirement.name)
        status = "matched" if resume_evidence else "missing"
        matched_count += int(resume_evidence is not None)
        evidence_items.append(
            MatchEvidence(
                requirement=requirement.name,
                requirement_type=("preferred_skill" if preferred else "required_skill"),
                status=status,
                job_evidence=requirement.evidence,
                resume_evidence=resume_evidence,
            )
        )
    available_maximum = maximum if selected else 0
    earned = maximum * matched_count / len(selected) if selected else 0
    label = "Preferred skills" if preferred else "Required skills"
    summary = (
        [f"Matched {matched_count}/{len(selected)} detected {label.casefold()}"]
        if selected
        else [f"No {label.casefold()} were reliably detected"]
    )
    score = _score_item(
        label.casefold().replace(" ", "_"),
        label,
        earned,
        available_maximum,
        summary,
    )
    return score, evidence_items


def _estimated_resume_years(resume_text: str) -> int | None:
    experience_text = _experience_section(resume_text)
    explicit = _year_values(experience_text)
    ranges: set[int] = set()
    for start_text, end_text in DATE_RANGE_PATTERN.findall(experience_text):
        start = int(start_text)
        end = (
            datetime.now(UTC).year
            if end_text.casefold() in {"present", "current"}
            else int(end_text)
        )
        if start <= end <= 2100:
            ranges.update(range(start, end))
    estimates = [*explicit, len(ranges)]
    return max(estimates) if estimates else None


def _experience_dimension(
    resume_text: str, job_description: str
) -> tuple[ScoreBreakdown, MatchEvidence | None]:
    requirements = _year_values(job_description)
    if not requirements:
        return _score_item(
            "experience", "Experience requirement", 0, 0, ["No explicit years requirement"]
        ), None
    required = max(requirements)
    estimated = _estimated_resume_years(resume_text)
    if estimated is None:
        earned = 0
        status = "unknown"
        resume_evidence = None
    else:
        earned = 20 * min(1, estimated / max(1, required))
        status = "matched" if estimated >= required else "partial"
        resume_evidence = f"Approximately {estimated} year(s) recognized from resume dates"
    evidence = MatchEvidence(
        requirement=f"{required}+ years of experience",
        requirement_type="experience",
        status=status,
        job_evidence=next(
            (
                line.strip()[:280]
                for line in job_description.splitlines()
                if YEARS_PATTERN.search(line)
            ),
            None,
        ),
        resume_evidence=resume_evidence,
    )
    return _score_item(
        "experience",
        "Experience requirement",
        earned,
        20,
        [resume_evidence or "Resume experience duration could not be estimated"],
    ), evidence


def _education_dimension(
    resume_text: str, job_description: str
) -> tuple[ScoreBreakdown, MatchEvidence | None]:
    for label, patterns in EDUCATION_RULES.items():
        job_evidence = _line_evidence(job_description, patterns)
        if job_evidence is None:
            continue
        resume_evidence = _line_evidence(resume_text, patterns)
        status = "matched" if resume_evidence else "missing"
        evidence = MatchEvidence(
            requirement=label,
            requirement_type="education",
            status=status,
            job_evidence=job_evidence,
            resume_evidence=resume_evidence,
        )
        return _score_item(
            "education",
            "Education requirement",
            10 if resume_evidence else 0,
            10,
            [resume_evidence or f"No evidence of {label} was recognized"],
        ), evidence
    return _score_item(
        "education", "Education requirement", 0, 0, ["No explicit education requirement"]
    ), None


def _category(text: str) -> JobCategory | None:
    normalized = normalize_for_matching(text)
    for category, patterns in CATEGORY_RULES:
        if contains_any(normalized, patterns):
            return category
    return None


def _role_dimension(
    resume_text: str, job_description: str
) -> tuple[ScoreBreakdown, MatchEvidence | None]:
    target = _category(job_description)
    if target is None:
        return _score_item(
            "role_alignment", "Role alignment", 0, 0, ["No role family reliably detected"]
        ), None
    resume_category = _category(resume_text)
    matched = resume_category == target
    evidence = MatchEvidence(
        requirement=target.value.replace("_", " "),
        requirement_type="role_alignment",
        status="matched" if matched else "missing",
        job_evidence=f"Target role family: {target.value}",
        resume_evidence=(
            f"Resume role family: {resume_category.value}" if resume_category else None
        ),
    )
    return _score_item(
        "role_alignment",
        "Role alignment",
        10 if matched else 0,
        10,
        [evidence.resume_evidence or "No matching resume role family detected"],
    ), evidence


def _tokens(text: str) -> Counter[str]:
    return Counter(
        token
        for token in TOKEN_PATTERN.findall(normalize_for_matching(text))
        if token not in STOPWORDS
    )


def _cosine_similarity(left: str, right: str) -> float:
    a = _tokens(left)
    b = _tokens(right)
    if not a or not b:
        return 0.0
    dot = sum(count * b.get(token, 0) for token, count in a.items())
    norm_a = math.sqrt(sum(count * count for count in a.values()))
    norm_b = math.sqrt(sum(count * count for count in b.values()))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def _eligibility_flags(job_description: str) -> list[str]:
    flags: list[str] = []
    for pattern in ELIGIBILITY_PATTERNS:
        evidence = _line_evidence(job_description, (pattern,))
        if evidence and evidence not in flags:
            flags.append(evidence)
    return flags[:8]


def score_job_match(resume_text: str, job_description: str) -> JobMatchResult:
    """Calculate a reproducible match score from source-backed signals."""

    requirements = _extract_skill_requirements(job_description)
    required_score, required_evidence = _skill_dimension(
        requirements, resume_text, preferred=False, maximum=35
    )
    preferred_score, preferred_evidence = _skill_dimension(
        requirements, resume_text, preferred=True, maximum=15
    )
    experience_score, experience_evidence = _experience_dimension(
        resume_text, job_description
    )
    education_score, education_evidence = _education_dimension(
        resume_text, job_description
    )
    role_score, role_evidence = _role_dimension(resume_text, job_description)
    similarity = _cosine_similarity(resume_text, job_description)
    semantic_score = _score_item(
        "semantic_relevance",
        "Normalized term relevance",
        similarity * 10,
        10,
        [f"Deterministic normalized token similarity: {similarity:.2f}"],
    )
    semantic_evidence = MatchEvidence(
        requirement="Overall responsibility and terminology alignment",
        requirement_type="semantic_relevance",
        status="matched" if similarity >= 0.35 else "partial" if similarity >= 0.15 else "missing",
        job_evidence="Normalized job-description terms",
        resume_evidence=f"Similarity {similarity:.2f}",
    )

    breakdown = [
        required_score,
        preferred_score,
        experience_score,
        education_score,
        role_score,
        semantic_score,
    ]
    evidence_items = [*required_evidence, *preferred_evidence]
    evidence_items.extend(
        item
        for item in (
            experience_evidence,
            education_evidence,
            role_evidence,
            semantic_evidence,
        )
        if item is not None
    )
    substantive_signal_available = any(
        item.status != "unavailable"
        for item in (
            required_score,
            preferred_score,
            experience_score,
            education_score,
        )
    )
    assessed = [item for item in breakdown if item.status != "unavailable"]
    earned = sum(item.earned for item in assessed)
    maximum = sum(item.maximum for item in assessed)
    score = (
        round(100 * earned / maximum)
        if maximum and substantive_signal_available
        else None
    )
    matched = [item for item in evidence_items if item.status == "matched"]
    partial = [item for item in evidence_items if item.status == "partial"]
    missing = [item for item in evidence_items if item.status == "missing"]
    unknowns = [item for item in evidence_items if item.status == "unknown"]
    suggestions = [
        f"If accurate, add concrete resume evidence for {item.requirement}."
        for item in missing[:5]
    ]
    if unknowns:
        suggestions.append(
            "Use clear Month Year date ranges so experience duration can be verified."
        )
    confidence = (
        "high"
        if len(requirements) >= 4 and maximum >= 55
        else "medium"
        if maximum >= 35
        else "limited"
    )
    if score is None:
        level = "Insufficient evidence"
        confidence = "limited"
        summary = (
            "The job description does not contain enough explicit skills, education, "
            "or experience requirements to calculate a defensible match score."
        )
    else:
        level = (
            "Strong match"
            if score >= 80
            else "Partial match"
            if score >= 55
            else "Low match"
        )
        summary = (
            f"Matched {len(matched)} of {len(evidence_items)} assessed signals; "
            f"{len(missing)} gap(s) and {len(unknowns)} unknown(s) remain."
        )
    return JobMatchResult(
        score=score,
        insufficient_evidence=score is None,
        level=level,
        confidence=confidence,
        summary=summary,
        breakdown=breakdown,
        matched=matched,
        partial_matches=partial,
        missing=missing,
        unknowns=unknowns,
        eligibility_flags=_eligibility_flags(job_description),
        suggestions=suggestions,
    )
