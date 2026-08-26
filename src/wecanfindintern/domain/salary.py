"""Cross-provider salary extraction from job descriptions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True, slots=True)
class ParsedSalary:
    interval: str
    minimum: Decimal | None
    maximum: Decimal | None
    currency: str
    source: str = "description"


_NUMBER = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_CURRENCY = r"(?:CA\$|C\$|US\$|CAD|USD|GBP|EUR|£|€|\$)"
_RANGE_PATTERN = re.compile(
    rf"(?P<prefix1>{_CURRENCY})?\s*(?P<minimum>{_NUMBER})\s*(?P<min_k>[kK])?"
    rf"\s*(?:-|–|—|to|through)\s*"
    rf"(?P<prefix2>{_CURRENCY})?\s*(?P<maximum>{_NUMBER})\s*(?P<max_k>[kK])?"
    rf"\s*(?P<suffix>CAD|USD|GBP|EUR)?",
    re.IGNORECASE,
)
_SINGLE_PATTERN = re.compile(
    rf"(?P<prefix>{_CURRENCY})\s*(?P<amount>{_NUMBER})\s*(?P<amount_k>[kK])?"
    rf"\s*(?P<suffix>CAD|USD|GBP|EUR)?",
    re.IGNORECASE,
)
_SALARY_SIGNAL = re.compile(
    r"salary|pay range|base pay|base salary|compensation|hourly rate|hourly wage|wage",
    re.IGNORECASE,
)
_EXCLUDED_SIGNAL = re.compile(
    r"bonus|commission|stock|equity|insurance|coverage|reimbursement|tuition",
    re.IGNORECASE,
)
_PERIOD_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("hourly", re.compile(r"(?:per|an|each)\s+hour|/\s*(?:hr|hour)|\bhourly\b", re.I)),
    ("daily", re.compile(r"(?:per|a|each)\s+day|/\s*day|\bdaily\b", re.I)),
    ("weekly", re.compile(r"(?:per|a|each)\s+week|/\s*(?:wk|week)|\bweekly\b", re.I)),
    ("monthly", re.compile(r"(?:per|a|each)\s+month|/\s*(?:mo|month)|\bmonthly\b", re.I)),
    (
        "yearly",
        re.compile(
            r"(?:per|a|each)\s+year|/\s*(?:yr|year)|\bannually\b|\bannual\b|"
            r"per annum|\bsalaried\b|pay type\s*:?\s*salary",
            re.I,
        ),
    ),
)
_LIMITS: dict[str, tuple[Decimal, Decimal]] = {
    "hourly": (Decimal("5"), Decimal("500")),
    "daily": (Decimal("40"), Decimal("5000")),
    "weekly": (Decimal("100"), Decimal("25000")),
    "monthly": (Decimal("500"), Decimal("100000")),
    "yearly": (Decimal("5000"), Decimal("2000000")),
}


def extract_salary_from_description(
    description: str | None,
    *,
    country_code: str | None = None,
) -> ParsedSalary | None:
    """Extract a defensible base-pay range from any provider's JD text."""

    if not description:
        return None
    candidates: list[tuple[int, ParsedSalary]] = []
    for window in _candidate_windows(description):
        interval = _detect_interval(window)
        if not interval:
            continue
        if _EXCLUDED_SIGNAL.search(window) and not _SALARY_SIGNAL.search(window):
            continue

        range_match = _RANGE_PATTERN.search(window)
        if range_match:
            minimum = _amount(range_match.group("minimum"), range_match.group("min_k"))
            maximum = _amount(range_match.group("maximum"), range_match.group("max_k"))
            if (range_match.group("min_k") or range_match.group("max_k")) and minimum < 1000:
                minimum *= 1000
            if (range_match.group("min_k") or range_match.group("max_k")) and maximum < 1000:
                maximum *= 1000
            currency = _detect_currency(
                " ".join(
                    filter(
                        None,
                        (
                            range_match.group("prefix1"),
                            range_match.group("prefix2"),
                            range_match.group("suffix"),
                            window,
                        ),
                    )
                ),
                country_code,
            )
            parsed = _validated(interval, minimum, maximum, currency)
            if parsed:
                score = 3 + int(bool(_SALARY_SIGNAL.search(window)))
                candidates.append((score, parsed))
                continue

        single_match = _SINGLE_PATTERN.search(window)
        if single_match and _SALARY_SIGNAL.search(window):
            amount = _amount(single_match.group("amount"), single_match.group("amount_k"))
            currency = _detect_currency(
                " ".join(
                    filter(
                        None,
                        (
                            single_match.group("prefix"),
                            single_match.group("suffix"),
                            window,
                        ),
                    )
                ),
                country_code,
            )
            parsed = _validated(interval, amount, None, currency)
            if parsed:
                candidates.append((2, parsed))

    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def has_salary_signal(description: str | None) -> bool:
    if not description:
        return False
    return bool(
        _SALARY_SIGNAL.search(description)
        or re.search(r"(?:CA\$|C\$|US\$|\$|£|€)\s*\d", description, re.I)
    )


def salary_signal_context(description: str, *, max_characters: int = 12_000) -> str:
    windows = _candidate_windows(description)
    return "\n\n---\n\n".join(windows)[:max_characters]


def validated_salary(
    *,
    interval: str,
    minimum: Decimal | None,
    maximum: Decimal | None,
    currency: str,
    source: str,
) -> ParsedSalary | None:
    return _validated(interval, minimum, maximum, currency, source=source)


def _candidate_windows(text: str) -> list[str]:
    compact = re.sub(r"[\t\r]+", " ", text)
    compact = re.sub(r"\n+", "\n", compact)
    signal = re.compile(
        rf"{_CURRENCY}|salary|pay range|base pay|base salary|compensation|hourly|annually|per annum",
        re.IGNORECASE,
    )
    windows: list[str] = []
    seen: set[str] = set()
    for match in signal.finditer(compact):
        start = max(0, match.start() - 140)
        end = min(len(compact), match.end() + 220)
        window = compact[start:end]
        if window not in seen:
            seen.add(window)
            windows.append(window)
    return windows


def _detect_interval(text: str) -> str | None:
    for interval, pattern in _PERIOD_PATTERNS:
        if pattern.search(text):
            return interval
    return None


def _detect_currency(text: str, country_code: str | None) -> str:
    upper = text.upper()
    if "CA$" in upper or "C$" in upper or re.search(r"\bCAD\b", upper):
        return "CAD"
    if "US$" in upper or re.search(r"\bUSD\b", upper):
        return "USD"
    if "£" in text or re.search(r"\bGBP\b", upper):
        return "GBP"
    if "€" in text or re.search(r"\bEUR\b", upper):
        return "EUR"
    return "CAD" if country_code == "CA" else "USD"


def _amount(value: str, suffix: str | None) -> Decimal:
    try:
        amount = Decimal(value.replace(",", ""))
    except InvalidOperation:
        return Decimal("0")
    return amount * 1000 if suffix else amount


def _validated(
    interval: str,
    minimum: Decimal | None,
    maximum: Decimal | None,
    currency: str,
    *,
    source: str = "description",
) -> ParsedSalary | None:
    if minimum is None and maximum is None:
        return None
    if minimum is not None and maximum is not None and minimum > maximum:
        return None
    lower, upper = _LIMITS[interval]
    values = [value for value in (minimum, maximum) if value is not None]
    if not all(lower <= value <= upper for value in values):
        return None
    return ParsedSalary(
        interval=interval,
        minimum=minimum,
        maximum=maximum,
        currency=currency,
        source=source,
    )
