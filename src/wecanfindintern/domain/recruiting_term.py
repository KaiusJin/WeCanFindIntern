"""Deterministic recruiting season extraction and LLM context selection."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

RecruitingSeason = Literal["winter", "spring", "summer", "fall"]

_SEASON_PATTERN = r"winter|win(?:ter)?|spring|spr(?:ing)?|summer|sum(?:mer)?|fall|autumn|aut"
_YEAR_PATTERN = r"20[2-9]\d|['’]\d{2}|[23]\d"
_FILLER_PATTERN = (
    r"(?:\s|,|;|/|\||:|\(|\)|\[|\]|\-|–|—)*"
    r"(?:(?:term|semester|session|intake|internship|co-op|coop|program|placement)"
    r"(?:\s|,|;|/|\||:|\(|\)|\[|\]|\-|–|—)*){0,3}"
)
_TERM_PATTERNS = (
    re.compile(
        rf"\b(?P<season>{_SEASON_PATTERN})\b{_FILLER_PATTERN}"
        rf"(?<!\d)(?P<year>{_YEAR_PATTERN})(?!\d)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?<!\d)(?P<year>{_YEAR_PATTERN})(?!\d){_FILLER_PATTERN}"
        rf"(?P<season>{_SEASON_PATTERN})\b",
        re.IGNORECASE,
    ),
)
_COMPACT_TERM_PATTERN = re.compile(
    r"\b(?:(?P<prefix>WI|SP|SU|FA|W|F)(?P<prefix_year>\d{2}|20\d{2})|"
    r"(?P<suffix_year>\d{2}|20\d{2})(?P<suffix>WI|SP|SU|FA|W|F))\b",
    re.IGNORECASE,
)
_MULTI_TERM_PATTERNS = (
    re.compile(
        rf"\b(?P<first>{_SEASON_PATTERN})\b\s*(?:/|&|\+|and)\s*"
        rf"\b(?P<second>{_SEASON_PATTERN})\b{_FILLER_PATTERN}"
        rf"(?<!\d)(?P<year>{_YEAR_PATTERN})(?!\d)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?<!\d)(?P<year>{_YEAR_PATTERN})(?!\d){_FILLER_PATTERN}"
        rf"(?P<first>{_SEASON_PATTERN})\b\s*(?:/|&|\+|and)\s*"
        rf"\b(?P<second>{_SEASON_PATTERN})\b",
        re.IGNORECASE,
    ),
)
_SEASON_SIGNAL = re.compile(rf"\b(?:{_SEASON_PATTERN})\b", re.IGNORECASE)
_YEAR_SIGNAL = re.compile(r"(?<!\d)(?:20[2-9]\d|['’]\d{2})(?!\d)")
_BARE_YEAR_TERM_SIGNAL = re.compile(
    rf"(?:\b(?:{_SEASON_PATTERN})\b.{{0,48}}(?<!\d)[23]\d(?!\d)|"
    rf"(?<!\d)[23]\d(?!\d).{{0,48}}\b(?:{_SEASON_PATTERN})\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class RecruitingTerm:
    season: RecruitingSeason
    year: int
    source: str
    evidence: str
    confidence: float

    @property
    def display_name(self) -> str:
        return f"{self.season.title()} {self.year}"


def recruiting_term_input_hash(title: str, description: str | None) -> str:
    content = f"{title}\n{description or ''}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def extract_recruiting_term_regex(
    title: str,
    description: str | None,
) -> RecruitingTerm | None:
    """Extract one explicit season/year pair, preferring the job title."""

    title_result = _extract_unique(title, source="regex_title")
    if title_result is not None:
        return title_result
    return _extract_unique(description or "", source="regex_description")


def recruiting_term_signal_context(
    title: str,
    description: str | None,
    *,
    max_chars: int = 5000,
) -> str | None:
    """Return only lines near explicit season/year signals, never the full JD."""

    selected = [f"Job title: {title.strip()}"]
    text = description or ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if not (_SEASON_SIGNAL.search(line) or _YEAR_SIGNAL.search(line)):
            continue
        start = max(0, index - 1)
        end = min(len(lines), index + 2)
        selected.extend(lines[start:end])
    context = "\n".join(dict.fromkeys(selected))[:max_chars]
    has_year = _YEAR_SIGNAL.search(context) or _BARE_YEAR_TERM_SIGNAL.search(context)
    if not (_SEASON_SIGNAL.search(context) and has_year):
        return None
    return context


def _extract_unique(text: str, *, source: str) -> RecruitingTerm | None:
    matches: dict[tuple[RecruitingSeason, int], str] = {}
    for pattern in _MULTI_TERM_PATTERNS:
        for match in pattern.finditer(text):
            year = _normalize_year(match.group("year"))
            matches.setdefault((_normalize_season(match.group("first")), year), match.group(0))
            matches.setdefault((_normalize_season(match.group("second")), year), match.group(0))
    for pattern in _TERM_PATTERNS:
        for match in pattern.finditer(text):
            season = _normalize_season(match.group("season"))
            year = _normalize_year(match.group("year"))
            matches.setdefault((season, year), match.group(0))
    compact_seasons = {
        "wi": "winter", "w": "winter", "sp": "spring", "su": "summer",
        "fa": "fall", "f": "fall",
    }
    for match in _COMPACT_TERM_PATTERN.finditer(text):
        code = (match.group("prefix") or match.group("suffix")).casefold()
        if len(code) == 1 and source != "regex_title":
            continue
        raw_year = match.group("prefix_year") or match.group("suffix_year")
        season = compact_seasons[code]
        matches.setdefault((season, _normalize_year(raw_year)), match.group(0))
    if len(matches) != 1:
        return None
    (season, year), evidence = next(iter(matches.items()))
    return RecruitingTerm(
        season=season,
        year=year,
        source=source,
        evidence=evidence,
        confidence=1.0,
    )


def _normalize_season(value: str) -> RecruitingSeason:
    normalized = value.casefold()
    aliases: dict[str, RecruitingSeason] = {
        "winter": "winter",
        "win": "winter",
        "spring": "spring",
        "spr": "spring",
        "summer": "summer",
        "sum": "summer",
        "fall": "fall",
        "autumn": "fall",
        "aut": "fall",
    }
    return aliases[normalized]


def _normalize_year(value: str) -> int:
    digits = value.lstrip("'’")
    year = int(digits)
    return 2000 + year if year < 100 else year
