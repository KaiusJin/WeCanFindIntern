"""Deterministic recruiting season and work-period normalization."""

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
_MONTH_NAMES = (
    "jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    "jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_DAY = r"(?:\s+\d{1,2}(?:st|nd|rd|th)?\b,?)?"
_MONTH_RANGE = re.compile(
    rf"\b(?P<start>{_MONTH_NAMES})\b{_DAY}"
    rf"(?:\s*,?\s*(?P<start_year>20[2-9]\d))?\s*"
    rf"(?:to|through|[-–—])\s*(?P<end>{_MONTH_NAMES})\b{_DAY}"
    rf"(?:\s*,?\s*(?P<end_year>20[2-9]\d))?",
    re.I,
)
_MONTH_DATE = re.compile(
    rf"\b(?P<month>{_MONTH_NAMES})\b{_DAY}\s*,?\s*(?P<year>20[2-9]\d)\b",
    re.I,
)
_MONTH_NUMBERS = {}
for _index, _name in enumerate(
    (
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    ),
    1,
):
    _MONTH_NUMBERS[_name] = _MONTH_NUMBERS[_name[:3]] = _index
_MONTH_NUMBERS["sept"] = 9


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

    return normalize_recruiting_term(title, description).primary_term


def extract_recruiting_terms_regex(
    title: str,
    description: str | None,
) -> list[RecruitingTerm]:
    """Normalize all declared terms, including four-month work-term ranges.

    January–April is Winter, May–August Summer, September–December Fall.
    Explicit named seasons (including Spring) are preserved as published.
    """
    return list(normalize_recruiting_term(title, description).covered_terms)


@dataclass(frozen=True, slots=True)
class RecruitingTermNormalization:
    primary_term: RecruitingTerm | None
    covered_terms: tuple[RecruitingTerm, ...] = ()
    coverage_inferred: bool = False
    score: int = 0


def _month_season(month: int) -> RecruitingSeason:
    return "winter" if month <= 4 else "summer" if month <= 8 else "fall"


def _date_field(text: str, start: int, end: int) -> str:
    # Dates in another sentence/field must not supply positive or negative cues.
    boundaries = list(re.finditer(r"\n|;|\.(?=\s+[A-Z])", text))
    left = max((m.end() for m in boundaries if m.end() <= start), default=0)
    right = min((m.start() for m in boundaries if m.start() >= end), default=len(text))
    field = text[left:right]
    previous = text[:left].rstrip().splitlines()
    if previous and re.fullmatch(
        r"[\s*#]*(?:start date|program dates|internship period)[\s*:]*", previous[-1], re.I
    ):
        field = previous[-1] + " " + field
    return field


def normalize_recruiting_term(
    title: str, description: str | None = None
) -> RecruitingTermNormalization:
    """Score work dates and distinguish start term from all covered work terms.

    Month-based coverage uses Winter Jan-Apr, Summer May-Aug, Fall Sep-Dec.
    Duration-only coverage is explicitly marked inferred; no year is invented.
    """
    candidates = []
    for text, source in [(title, "regex_title"), (description or "", "regex_description")]:
        raw = []
        multi_spans = []
        for pattern in _MULTI_TERM_PATTERNS:
            for match in pattern.finditer(text):
                year = _normalize_year(match.group("year"))
                raw.append(
                    (
                        match,
                        [
                            (_normalize_season(match.group("first")), year),
                            (_normalize_season(match.group("second")), year),
                        ],
                        False,
                        True,
                    )
                )
                multi_spans.append(match.span())
        for pattern in _TERM_PATTERNS:
            for match in pattern.finditer(text):
                if any(match.start() < b and a < match.end() for a, b in multi_spans):
                    continue
                raw.append(
                    (
                        match,
                        [
                            (
                                _normalize_season(match.group("season")),
                                _normalize_year(match.group("year")),
                            )
                        ],
                        False,
                        True,
                    )
                )
        codes = {
            "wi": "winter",
            "w": "winter",
            "sp": "spring",
            "su": "summer",
            "fa": "fall",
            "f": "fall",
        }
        for match in _COMPACT_TERM_PATTERN.finditer(text):
            code = (match.group("prefix") or match.group("suffix")).lower()
            if len(code) == 1 and source != "regex_title":
                continue
            year = _normalize_year(match.group("prefix_year") or match.group("suffix_year"))
            raw.append((match, [(codes[code], year)], False, True))
        ranges = list(_MONTH_RANGE.finditer(text))
        for match in ranges:
            first = _MONTH_NUMBERS[match.group("start").lower()]
            last = _MONTH_NUMBERS[match.group("end").lower()]
            year1 = match.group("start_year") or match.group("end_year")
            year2 = match.group("end_year") or year1
            if year1 is None:
                continue
            start, end = int(year1) * 12 + first - 1, int(year2) * 12 + last - 1
            if not 0 <= end - start <= 24:
                continue
            terms = list(
                dict.fromkeys((_month_season(m % 12 + 1), m // 12) for m in range(start, end + 1))
            )
            raw.append((match, terms, False, False))
        for match in _MONTH_DATE.finditer(text):
            if any(match.start() < r.end() and r.start() < match.end() for r in ranges):
                continue
            month = _MONTH_NUMBERS[match.group("month").lower()]
            year = int(match.group("year"))
            duration = re.match(
                r"\s*[,;]?\s*(?:for\s+)?(\d{1,2})\s+(months?|weeks?)\b",
                text[match.end() : match.end() + 40],
                re.I,
            )
            months = 1
            if duration:
                length = int(duration.group(1))
                months = (
                    length
                    if duration.group(2).lower().startswith("month")
                    else (length * 7 + 29) // 30
                )
                months = max(1, min(months, 24))
            start = year * 12 + month - 1
            terms = list(
                dict.fromkeys(
                    (_month_season(m % 12 + 1), m // 12) for m in range(start, start + months)
                )
            )
            raw.append((match, terms, bool(duration), False))
        for match, terms, inferred, named in raw:
            field = _date_field(text, match.start(), match.end())
            relative = field.find(match.group(0))
            if relative < 0:
                continue
            near60 = field[max(0, relative - 60) : relative + len(match.group(0)) + 60]
            near80 = field[max(0, relative - 80) : relative + len(match.group(0)) + 80]
            starts = bool(
                re.search(
                    r"\b(?:internship starts?|start date|starting|begins?|beginning|"
                    r"expected to start|job assignment)\b",
                    near60,
                    re.I,
                )
            )
            period = bool(re.search(r"\b(?:program dates|internship period)\b", near80, re.I))
            available = bool(re.search(r"\bavailable (?:to work|for)\b", near60, re.I))
            intern = bool(re.search(r"\b(?:intern(?:ship)?|co-op)\b", near80, re.I))
            score = 10 if source == "regex_title" else 3 if named else 0
            score += 8 * starts + 8 * period + 5 * available + 3 * intern
            standalone = text.strip() == match.group(0).strip()
            if inferred:
                remainder = text[match.end() :].strip()
                standalone = (
                    standalone
                    or bool(
                        re.fullmatch(
                            r"[,;]?\s*(?:for\s+)?\d{1,2}\s+(?:months?|weeks?)",
                            remainder,
                            re.I,
                        )
                    )
                    and not text[: match.start()].strip()
                )
            if standalone and not named:
                score = max(score, 6)
            score -= 10 * bool(re.search(r"\b(?:graduat\w*|graduate by)\b", near80, re.I))
            score -= 12 * bool(
                re.search(
                    r"\b(?:application deadline|deadline|apply by|applications? close|"
                    r"posting date|posted on)\b|^\s*(?:\*\*)?date\s*:",
                    near80,
                    re.I,
                )
            )
            score -= 6 * bool(re.search(r"\bbenefits effective\b", near80, re.I))
            if score < 6:
                continue
            covered = tuple(RecruitingTerm(s, y, source, match.group(0), 1.0) for s, y in terms)
            # A named A/B term does not establish which season starts first.
            primary = None if named and len(covered) > 1 else covered[0]
            priority = (
                0 if starts or period else 1 if source == "regex_title" else 2 if inferred else 3
            )
            candidates.append(
                (
                    priority,
                    -score,
                    match.start(),
                    RecruitingTermNormalization(primary, covered, inferred, score),
                )
            )
    if not candidates:
        return RecruitingTermNormalization(None)
    candidates.sort(key=lambda item: item[:3])
    best = candidates[0]
    ties = [c[3] for c in candidates if c[:2] == best[:2]]
    keys = {(c.primary_term.season, c.primary_term.year) if c.primary_term else None for c in ties}
    if len(keys) > 1:
        return RecruitingTermNormalization(None)
    return best[3]


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
