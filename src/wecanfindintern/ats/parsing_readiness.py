"""Deterministic resume parsing-readiness diagnostics."""

from __future__ import annotations

import re
from collections.abc import Sequence

from wecanfindintern.ats.models import ParsingReadinessResult, ScoreBreakdown

SECTION_PATTERNS: dict[str, tuple[str, ...]] = {
    "Experience": ("experience", "employment", "work history"),
    "Education": ("education", "academic background"),
    "Skills": ("skills", "technical skills", "technologies"),
    "Projects": ("projects", "selected projects", "personal projects"),
    "Summary": ("summary", "profile", "objective"),
    "Certifications": ("certifications", "certificates", "licenses"),
}
SECTION_POINTS = {
    "Experience": 6,
    "Education": 6,
    "Skills": 5,
    "Projects": 4,
    "Summary": 2,
    "Certifications": 2,
}
EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
PHONE_PATTERN = re.compile(r"(?:\+?\d[\d().\-\s]{7,}\d)")
DATE_PATTERN = re.compile(
    r"\b(?:19|20)\d{2}\b|\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|"
    r"may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)\s+(?:19|20)?\d{2}\b",
    re.IGNORECASE,
)
DATE_RANGE_PATTERN = re.compile(
    r"(?:19|20)\d{2}\s*(?:[-–—]|to)\s*(?:(?:19|20)\d{2}|present|current)",
    re.IGNORECASE,
)
BULLET_PATTERN = re.compile(r"(?m)^\s*(?:[•●▪◦*-]|\d+[.)])\s+\S")


def _item(
    category: str,
    label: str,
    earned: float,
    maximum: float,
    evidence: list[str],
    *,
    unavailable: bool = False,
) -> ScoreBreakdown:
    if unavailable:
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
        earned=earned,
        maximum=maximum,
        status=status,
        evidence=evidence,
    )


def _section_names(text: str) -> list[str]:
    lines = [re.sub(r"[^a-z ]", "", line.casefold()).strip() for line in text.splitlines()]
    return [
        label
        for label, variants in SECTION_PATTERNS.items()
        if any(line in variants for line in lines)
    ]


def _has_phone(text: str) -> bool:
    return any(
        len(re.sub(r"\D", "", match.group(0))) >= 10
        for match in PHONE_PATTERN.finditer(text)
    )


def score_parsing_readiness(
    text: str,
    *,
    page_texts: Sequence[str] | None = None,
) -> ParsingReadinessResult:
    """Score observable extraction evidence without asking an LLM to grade it."""

    cleaned = text.strip()
    compact_chars = len(re.sub(r"\s+", "", cleaned))
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    mode = "pdf_layout" if page_texts is not None else "text_only"
    breakdown: list[ScoreBreakdown] = []
    issues: list[str] = []
    limitations: list[str] = []

    extraction_points = 30
    extraction_evidence = [f"{compact_chars:,} non-whitespace characters extracted"]
    if compact_chars < 200:
        extraction_points = 5
        issues.append("Very little machine-readable text was extracted.")
    elif compact_chars < 500:
        extraction_points = 15
        issues.append("The extracted resume text is unusually short.")
    elif compact_chars < 1000:
        extraction_points = 24
    replacement_count = cleaned.count("�")
    if replacement_count:
        extraction_points = max(0, extraction_points - min(10, replacement_count))
        extraction_evidence.append(f"{replacement_count} replacement characters detected")
        issues.append("Some characters could not be decoded cleanly.")
    if page_texts is not None:
        empty_pages = sum(not re.sub(r"\s+", "", page) for page in page_texts)
        extraction_evidence.append(
            f"{len(page_texts) - empty_pages}/{len(page_texts)} pages contain text"
        )
        if empty_pages:
            extraction_points = max(0, extraction_points - empty_pages * 6)
            issues.append("One or more PDF pages contain no extractable text.")
    breakdown.append(
        _item(
            "text_extraction", "Text extraction", extraction_points, 30, extraction_evidence
        )
    )

    sections = _section_names(cleaned)
    section_points = sum(SECTION_POINTS[name] for name in sections)
    if "Experience" not in sections and "Projects" not in sections:
        issues.append("No standard Experience or Projects heading was recognized.")
    breakdown.append(
        _item(
            "section_recognition",
            "Section recognition",
            section_points,
            25,
            [f"Recognized sections: {', '.join(sections) or 'none'}"],
        )
    )

    contact_points = 0
    contact_evidence: list[str] = []
    if EMAIL_PATTERN.search(cleaned):
        contact_points += 4
        contact_evidence.append("Email address recognized")
    if _has_phone(cleaned):
        contact_points += 3
        contact_evidence.append("Phone number recognized")
    links = [
        label
        for label in ("linkedin", "github", "portfolio")
        if label in cleaned.casefold()
    ]
    contact_points += min(3, len(links))
    if links:
        contact_evidence.append(f"Profile links recognized: {', '.join(links)}")
    if contact_points < 7:
        issues.append("Some standard contact information was not recognized.")
    breakdown.append(
        _item(
            "contact_parsing",
            "Contact parsing",
            contact_points,
            10,
            contact_evidence or ["No standard contact fields recognized"],
        )
    )

    bullet_count = len(BULLET_PATTERN.findall(cleaned))
    date_count = len(DATE_PATTERN.findall(cleaned))
    structure_points = min(9, bullet_count) + min(6, date_count)
    breakdown.append(
        _item(
            "entry_structure",
            "Entry structure",
            structure_points,
            15,
            [f"{bullet_count} bullets and {date_count} date references recognized"],
        )
    )

    if page_texts is None:
        limitations.append(
            "Text-only input cannot assess PDF columns, page reading order, or empty pages."
        )
        breakdown.append(
            _item(
                "reading_order",
                "PDF reading order",
                0,
                10,
                ["Upload the PDF to assess page-level extraction."],
                unavailable=True,
            )
        )
    else:
        short_lines = sum(len(line) <= 2 for line in lines)
        fragmented_ratio = short_lines / max(1, len(lines))
        wide_gap_lines = sum(bool(re.search(r"\S\s{3,}\S", line)) for line in lines)
        wide_gap_ratio = wide_gap_lines / max(1, len(lines))
        reading_points = 10
        if fragmented_ratio > 0.2:
            reading_points -= 6
        elif fragmented_ratio > 0.08:
            reading_points -= 3
        if wide_gap_ratio > 0.25:
            reading_points -= 4
            issues.append("Wide text gaps suggest a possible multi-column reading order.")
        table_like_lines = sum(bool(re.search(r"\|.*\|.*\|", line)) for line in lines)
        if table_like_lines >= 2:
            reading_points -= 2
            issues.append("Pipe-separated text suggests a possible table structure.")
        reading_points = max(0, reading_points)
        if reading_points < 10:
            issues.append("Extracted reading order contains fragmented lines.")
        breakdown.append(
            _item(
                "reading_order",
                "PDF reading order",
                reading_points,
                10,
                [
                    f"{fragmented_ratio:.0%} of extracted lines are fragmented",
                    f"{wide_gap_ratio:.0%} contain wide internal gaps",
                ],
            )
        )

    range_count = len(DATE_RANGE_PATTERN.findall(cleaned))
    chronology_points = min(10, date_count * 2 + range_count * 2)
    if chronology_points < 5:
        issues.append("Dates or date ranges could not be reliably recognized.")
    breakdown.append(
        _item(
            "date_consistency",
            "Date consistency",
            chronology_points,
            10,
            [f"{date_count} dates and {range_count} date ranges recognized"],
        )
    )

    assessed = [item for item in breakdown if item.status != "unavailable"]
    earned = sum(item.earned for item in assessed)
    maximum = sum(item.maximum for item in assessed)
    score = round(100 * earned / maximum) if maximum else 0
    level = "Strong" if score >= 80 else "Needs review" if score >= 55 else "High risk"
    confidence = (
        "limited"
        if mode == "text_only"
        else "high"
        if compact_chars >= 1000
        else "medium"
    )
    return ParsingReadinessResult(
        score=score,
        level=level,
        confidence=confidence,
        mode=mode,
        summary=(
            f"The resume earned {round(earned)}/{round(maximum)} assessed points. "
            f"{len(issues)} parsing issue(s) need attention."
        ),
        breakdown=breakdown,
        parsed_sections=sections,
        issues=issues,
        limitations=limitations,
    )
