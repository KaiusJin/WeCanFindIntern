"""Title and country filters for ATS postings.

A board returns every opening of a company (sales, senior roles, other
countries), so ATS postings are filtered locally instead of by search keywords.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from wecanfindintern.domain.location import (
    CANADIAN_REGION_ALIASES,
    US_REGION_ALIASES,
    parse_location,
)
from wecanfindintern.domain.normalization import normalize_text

# Strong signals mark a CS role even next to a business word ("Sales Data Analytics Intern").
CS_TITLE = re.compile(
    r"software|\bswe\b|\bsde\b|develop(?:er|ment engineer)|programmer|back[- ]?end|front[- ]?end|"
    r"full[- ]?stack|\bweb\b|mobile|\bios\b|android|devops|\bsre\b|site reliability|platform|"
    r"infrastructure|cloud|\bdata\b|analytics|machine learning|\bml\b|\bai\b|"
    r"artificial intelligence|deep learning|computer|computational|\bnlp\b|\bllm|"
    r"research engineer|applied scien|security|cyber|\bqa\b|quality assurance|test automation|"
    r"\bsdet\b|embedded|firmware|systems? (?:software|engineer)|quant\w*|trading|trader|"
    r"algorithm|compiler|robot\w*|autonom\w*|perception|reinforcement|neural|"
    r"forward deployed|solutions engineer|business intelligence|\bgis\b|automation|"
    r"information (?:technology|systems)|\bit\b",
    re.I,
)
# Weak signals ("Research Intern", "Engineering Intern") count unless a non-CS word is present.
WEAK_CS_TITLE = re.compile(
    r"research\w*|scientist|science|technolog\w*|analyst|applied|model\w*|simulation|digital|"
    r"\bphd\b",
    re.I,
)
NON_CS_TITLE = re.compile(
    r"mechanical|civil|chemical|structural|electrical|hardware|\bfpga\b|\basic\b|analog|"
    r"\brf\b|marketing|\bsales\b|product manag|product design|\bux\b|supply chain|"
    r"manufacturing|finance|financial|policy|\btax\b|biolog|clinical|chemist|materials|"
    r"process engineer|industrial|environmental|nuclear|aerospace engineer|thermal|cryogenic|"
    r"spacecraft|propulsion|avionics|economics|consulting|investment|project manag|"
    r"\bbusiness analyst",
    re.I,
)
ALWAYS_REJECT_TITLE = re.compile(
    r"recruit|accounting|\blegal\b|\bmba\b|graphic design|investment banking|human resources|"
    r"\bhr\b|paralegal|nurs(?:e|ing)",
    re.I,
)
INTERN_TITLE = re.compile(
    r"\b(?:intern|interns|internship|co-?op|coop|student|studentship|apprentice\w*|fellows?|"
    r"fellowship|placement|resident|residency|trainee|summer analyst)\b",
    re.I,
)
NEW_GRAD_TITLE = re.compile(
    r"new[- ]?grad|new graduate|university grad|recent grad|early[- ]career|entry[- ]level|"
    r"\bjunior\b|\bjr\.?\b|campus|graduate (?:software|engineer|developer|program)",
    re.I,
)
SENIOR_TITLE = re.compile(
    r"\b(?:senior|sr\.?|staff|principal|lead|manager|director|head|architect|vp)\b|\b(?:ii|iii|iv)\b",
    re.I,
)

US_COUNTRY = re.compile(
    r"\bunited states(?: of america)?\b|\busa\b|\bu\.s\.a?\b|\bus\b|\bamerica\b", re.I
)
CA_COUNTRY = re.compile(r"\bcanada\b", re.I)
US_CITIES = {
    "san francisco",
    "new york",
    "new york city",
    "nyc",
    "seattle",
    "austin",
    "boston",
    "chicago",
    "los angeles",
    "palo alto",
    "mountain view",
    "sunnyvale",
    "san jose",
    "menlo park",
    "redwood city",
    "san mateo",
    "santa clara",
    "cupertino",
    "denver",
    "atlanta",
    "washington dc",
    "washington d c",
    "pittsburgh",
    "san diego",
    "bellevue",
    "redmond",
    "kirkland",
    "miami",
    "dallas",
    "houston",
    "philadelphia",
    "boulder",
    "salt lake city",
    "raleigh",
    "durham",
    "portland",
    "minneapolis",
    "detroit",
    "phoenix",
    "nashville",
    "columbus",
    "ann arbor",
    "cambridge ma",
    "sf bay area",
    "bay area",
}
CA_CITIES = {
    "toronto",
    "vancouver",
    "montreal",
    "montréal",
    "waterloo",
    "kitchener",
    "ottawa",
    "calgary",
    "edmonton",
    "quebec city",
    "halifax",
    "victoria",
    "winnipeg",
    "mississauga",
    "markham",
    "burnaby",
    "london ontario",
    "hamilton",
    "regina",
    "saskatoon",
}
COUNTRY_NAMES = {"US": "United States", "CA": "Canada"}


def title_kind(title: str, *, include_new_grad: bool, intern_hint: bool = False) -> str | None:
    """Return "internship" / "new_grad" for in-scope CS titles, else None.

    Recall matters more than precision here: an occasional non-CS internship is
    acceptable, a missed CS internship is not.
    """
    if ALWAYS_REJECT_TITLE.search(title):
        return None
    strong = bool(CS_TITLE.search(title))
    weak = bool(WEAK_CS_TITLE.search(title)) and not NON_CS_TITLE.search(title)
    if not (strong or weak):
        return None
    if intern_hint or INTERN_TITLE.search(title):
        return "internship"
    if include_new_grad and NEW_GRAD_TITLE.search(title) and not SENIOR_TITLE.search(title):
        return "new_grad"
    return None


_SPLIT = re.compile(r"[;|/\n]| or | and |·")


def _word_pattern(names: Iterable[str]) -> re.Pattern[str]:
    alternatives = sorted({re.escape(name) for name in names}, key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(alternatives) + r")\b")


_CA_WORDS = _word_pattern(
    {*CA_CITIES, *(name for name in CANADIAN_REGION_ALIASES if len(name) > 2)}
)
_US_WORDS = _word_pattern(
    {*US_CITIES, *(name for name in US_REGION_ALIASES if len(name) > 2 and name != "georgia")}
)


def _chunks(text: str | None) -> list[str]:
    return [c.strip() for c in _SPLIT.split(text or "") if c.strip()]


def _chunk_countries(chunk: str) -> set[str]:
    parts = [normalize_text(p) for p in chunk.split(",") if p.strip()]
    # "Palo Alto, CA" means California; the shared parser reads a trailing "CA" as Canada.
    if len(parts) >= 2 and parts[-1] == "ca":
        return {"CA"} if parts[0] in CA_CITIES else {"US"}
    found: set[str] = set()
    if CA_COUNTRY.search(chunk):
        found.add("CA")
    if US_COUNTRY.search(chunk):
        found.add("US")
    if found:
        return found
    parsed = parse_location(chunk).country_code
    if parsed in COUNTRY_NAMES:
        return {parsed}
    for index, part in enumerate(parts):
        if part in US_CITIES:
            found.add("US")
        elif part in CA_CITIES or index > 0 and part in CANADIAN_REGION_ALIASES and len(part) > 2:
            found.add("CA")
        elif index > 0 and part in US_REGION_ALIASES:
            found.add("US")
    if not found:
        # Looser text such as "Calgary Headquarters" or "Remote - Ontario".
        normalized = normalize_text(chunk)
        if _CA_WORDS.search(normalized):
            found.add("CA")
        elif _US_WORDS.search(normalized):
            found.add("US")
    return found


def countries_in(text: str | None) -> set[str]:
    """Best-effort US/CA detection for free-text ATS locations (may return both)."""
    found: set[str] = set()
    for chunk in _chunks(text):
        found |= _chunk_countries(chunk)
    return found


_COUNTRY_FIRST = re.compile(
    r"^\s*(?:us|usa|united states(?: of america)?|can|canada)\s*[,-]\s*([a-z]{2})\s*[,-]\s*(.+)$",
    re.I,
)


def _with_country(chunk: str, code: str) -> str:
    # Workday writes "US, CA, Santa Clara" (country, region, city).
    reordered = _COUNTRY_FIRST.match(chunk)
    if reordered:
        return f"{reordered[2].strip()}, {reordered[1].upper()}, {COUNTRY_NAMES[code]}"
    if parse_location(chunk).country_code == code:
        return chunk
    base = (CA_COUNTRY if code == "CA" else US_COUNTRY).sub("", chunk)
    base = re.sub(r"\(\s*\)", "", base).strip(" ,-–—.")
    return f"{base}, {COUNTRY_NAMES[code]}" if base else COUNTRY_NAMES[code]


def pick_location(candidates: Iterable[str | None], countries: set[str]) -> str | None:
    """One display location that names its country explicitly, so the shared
    parser cannot mistake a California "CA" for Canada."""
    chunks = [chunk for text in candidates for chunk in _chunks(text)]
    for chunk in chunks:
        own = _chunk_countries(chunk) & countries
        if len(own) == 1:
            return _with_country(chunk, own.pop())
    if len(countries) == 1:
        country = COUNTRY_NAMES[next(iter(countries))]
        return f"{chunks[0]}, {country}" if chunks else country
    return chunks[0] if chunks else None
