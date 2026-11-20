"""Cross-provider salary extraction from job descriptions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from wecanfindintern.domain.normalization import annualize_salary, to_decimal


@dataclass(frozen=True, slots=True)
class ParsedSalary:
    interval: str
    minimum: Decimal | None
    maximum: Decimal | None
    currency: str
    source: str = "description"


def format_salary_text(
    minimum: object,
    maximum: object,
    *,
    currency: str | None = None,
    interval: str | None = None,
) -> str | None:
    """Render structured salary values using one Tracker display contract."""

    minimum_value = to_decimal(minimum)
    maximum_value = to_decimal(maximum)
    if minimum_value is None and maximum_value is None:
        return None
    annual_minimum = annualize_salary(minimum_value, interval)
    annual_maximum = annualize_salary(maximum_value, interval)
    hourly_minimum = (
        minimum_value
        if interval == "hourly"
        else annual_minimum / Decimal("2080")
        if annual_minimum is not None
        else None
    )
    hourly_maximum = (
        maximum_value
        if interval == "hourly"
        else annual_maximum / Decimal("2080")
        if annual_maximum is not None
        else None
    )
    if hourly_minimum is None and hourly_maximum is None:
        return None
    symbol = {"CAD": "$", "USD": "$", "GBP": "£", "EUR": "€"}.get(currency or "", "$")
    if hourly_minimum is not None and hourly_maximum is not None:
        amount = f"{symbol}{hourly_minimum:.2f}–{symbol}{hourly_maximum:.2f}"
    elif hourly_minimum is not None:
        amount = f"from {symbol}{hourly_minimum:.2f}"
    else:
        amount = f"up to {symbol}{hourly_maximum:.2f}"
    parts = [currency, amount, "/hour"]
    return " ".join(part for part in parts if part)


_NUMBER = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_CURRENCY_CODE = r"(?<![A-Za-z_])(?:CAD|USD)(?![A-Za-z_])"
_CURRENCY = rf"(?:(?<![A-Za-z_])(?:CA|C|US)\$|{_CURRENCY_CODE}(?:\s*\$)?|\$)"
_UNSUPPORTED_CURRENCY = re.compile(r"(?<![A-Za-z_])(?:GBP|EUR|JPY|CNY)(?![A-Za-z_])|£|€|¥", re.I)
_RANGE_PATTERN = re.compile(
    rf"(?P<prefix1>{_CURRENCY})?\s*(?P<minimum>{_NUMBER})\s*(?P<min_k>[kKmM](?![A-Za-z]))?"
    rf"\s*(?:-|–|—|to|through)\s*"
    rf"(?P<prefix2>{_CURRENCY})?\s*(?P<maximum>{_NUMBER})\s*(?P<max_k>[kKmM](?![A-Za-z]))?"
    rf"\s*(?P<suffix>{_CURRENCY_CODE})?",
    re.IGNORECASE,
)
_SINGLE_PATTERN = re.compile(
    rf"(?<![\w.])(?P<prefix>{_CURRENCY})?\s*(?P<amount>{_NUMBER})"
    rf"\s*(?P<amount_k>[kKmM](?![A-Za-z]))?"
    rf"(?:\s*(?P<suffix>{_CURRENCY_CODE}))?(?!\w|\.\d)",
    re.I,
)
_SALARY_SIGNAL = re.compile(
    r"\b(?:salary|pay|pay range|base pay|base salary|compensation|hourly rate|hourly wage|wage)\b",
    re.IGNORECASE,
)
_PERIOD_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "biweekly",
        re.compile(
            r"\b(?:bi[ -]?weekly|fortnightly|(?:per|a|each|every)\s+fortnight|"
            r"(?:per|each|every)\s+(?:two|2)\s+weeks|every\s+other\s+week)\b|"
            r"/\s*(?:2\s*weeks|fortnight)\b|每两周|每两星期|每隔一周|双周薪?",
            re.I,
        ),
    ),
    (
        "hourly",
        re.compile(
            r"\b(?:per|an|each|every)\s+hour\b|/\s*(?:h|hrs?|hours?)\b|"
            r"\bhourly\b|每小时|时薪",
            re.I,
        ),
    ),
    (
        "daily",
        re.compile(r"\b(?:per|a|each|every)\s+day\b|/\s*days?\b|\bdaily\b|每天|每日|日薪", re.I),
    ),
    (
        "weekly",
        re.compile(
            r"\b(?:per|a|each|every)\s+week\b|/\s*(?:wks?|weeks?)\b|"
            r"\bweekly\b|每周|周薪",
            re.I,
        ),
    ),
    (
        "monthly",
        re.compile(
            r"\b(?:per|a|each|every)\s+month\b|/\s*(?:mos?|months?)\b|"
            r"\bmonthly\b|每月|月薪",
            re.I,
        ),
    ),
    (
        "yearly",
        re.compile(
            r"\b(?:per|a|each|every)\s+year\b|/\s*(?:yrs?|years?)\b|"
            r"\b(?:yearly|annually|annual|salaried)\b|"
            r"\bper annum\b|\bpay type\s*:?\s*salary\b|每年|年薪",
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


@dataclass(frozen=True, slots=True)
class SalaryCandidate:
    """Auditable amount candidate; only accepted base salary is returned to callers."""

    text: str
    start: int
    end: int
    score: int
    category: str
    salary: ParsedSalary | None


def extract_salary_from_description(
    description: str | None,
    *,
    country_code: str | None = None,
) -> ParsedSalary | None:
    """Select one base salary with score >= 6; unknown pay remains None."""
    candidates = sorted(
        (
            c
            for c in salary_candidates(description, country_code=country_code)
            if c.category == "base" and c.score >= 6 and c.salary is not None
        ),
        key=lambda c: (-c.score, c.start),
    )
    if not candidates:
        return None
    currencies = {c.salary.currency for c in candidates}
    if len(currencies) > 1:
        if country_code not in {"CA", "US"}:
            return None
        currency = "CAD" if country_code == "CA" else "USD"
        candidates = [c for c in candidates if c.salary.currency == currency]
    return candidates[0].salary if candidates else None


def salary_candidates(
    description: str | None,
    *,
    country_code: str | None = None,
) -> list[SalaryCandidate]:
    """Scan amounts first, then score their local context; never return plain numbers."""
    if not description:
        return []
    matches = []
    ranges = list(_RANGE_PATTERN.finditer(description))
    for match in ranges:
        if _money_evidence(match.group(0)):
            matches.append((match, True))
    for match in _SINGLE_PATTERN.finditer(description):
        if not _money_evidence(match.group(0)):
            continue
        if any(match.start() < r.end() and r.start() < match.end() for r in ranges):
            continue
        matches.append((match, False))
    matches.sort(key=lambda item: item[0].start())
    results = []
    for index, (match, is_range) in enumerate(matches):
        start, end = match.span()
        start += len(match.group(0)) - len(match.group(0).lstrip())
        end -= len(match.group(0)) - len(match.group(0).rstrip())
        groups = match.groupdict()
        if is_range:
            lo_suffix = groups["min_k"] or groups["max_k"]
            hi_suffix = groups["max_k"] or groups["min_k"]
            minimum = _amount(groups["minimum"], lo_suffix)
            maximum = _amount(groups["maximum"], hi_suffix)
            codes = " ".join(filter(None, match.group("prefix1", "prefix2", "suffix")))
        else:
            minimum = _amount(groups["amount"], groups["amount_k"])
            maximum = None
            codes = " ".join(filter(None, match.group("prefix", "suffix")))
        strong = description[max(0, start - 80) : end + 80]
        period_context = description[max(0, start - 50) : end + 30]
        local_span = (start - max(0, start - 50), end - max(0, start - 50))
        interval = _detect_interval(period_context, local_span)
        explicit = bool(re.search(_CURRENCY, match.group(0), re.I))
        strong_hit = bool(_SALARY_SIGNAL.search(strong))
        if is_range and re.search(
            r"\bsalary range\b[^\n]{0,100}$", description[max(0, start - 100) : start], re.I
        ):
            strong_hit = True
        section = _in_salary_section(description[:start])
        score = 2 * explicit + 4 * strong_hit + 4 * bool(interval) + 2 * is_range + 3 * section
        # Give an exclusion to its nearest amount, not every amount in a large
        # shared window ("$25/hour plus $5,000 signing bonus").
        left = max(0, start - 45)
        right = min(len(description), end + 45)
        if index:
            left = max(left, (matches[index - 1][0].end() + start) // 2)
        if index + 1 < len(matches):
            right = min(right, (end + matches[index + 1][0].start()) // 2)
        boundary_offset = left
        for boundary in re.finditer(r"[;\n]|\.(?=\s+[A-Z])", description[left:right]):
            boundary_start = boundary_offset + boundary.start()
            boundary_end = boundary_offset + boundary.end()
            if boundary_end <= start:
                left = boundary_end
            elif boundary_start >= end:
                right = boundary_start
                break
        exclusion = description[left:right]
        category = "base"
        if minimum == 0 and (maximum is None or maximum == 0):
            category, score = "placeholder", score - 10
        elif re.search(r"\b(?:revenue|sales|gpa|graduat\w*|401\s*\(?k\)?)\b", exclusion, re.I):
            category, score = "non_salary", score - 8
        elif re.search(r"\b(?:bonus|commission|equity|stock)\b", exclusion, re.I):
            category, score = "bonus", score - 5
        elif re.search(
            r"\b(?:stipend|allowance|reimbursement|tuition|insurance|spending account)\b",
            exclusion,
            re.I,
        ):
            category, score = "allowance", score - 5
        if _UNSUPPORTED_CURRENCY.search(description[max(0, start - 8) : end + 8]):
            category = "unsupported_currency"
        interval = interval or _infer_interval(minimum, maximum)
        currency = _detect_currency(codes, country_code)
        if not re.search(_CURRENCY_CODE + r"|(?<![A-Za-z_])(?:CA|C|US)\$", codes, re.I):
            # Only the nearby field may supply a code for an otherwise bare $.
            currency = _detect_currency(description[max(0, start - 15) : end + 15], country_code)
        salary = _validated(interval, minimum, maximum, currency) if interval else None
        results.append(SalaryCandidate(match.group(0).strip(), start, end, score, category, salary))
    return results


def _money_evidence(text: str) -> bool:
    return bool(re.search(_CURRENCY, text, re.I) or re.search(r"\d,\d|\d[kKmM]\b", text))


def _in_salary_section(prefix: str) -> bool:
    for line in reversed(prefix[-1000:].splitlines()):
        stripped = line.strip().strip("*# ")
        if re.search(
            r"\b(?:salary|salaries|compensation|pay)\b.*"
            r"\b(?:listed|shown|provided|displayed)\s+below\b",
            stripped,
            re.I,
        ):
            return True
        if not stripped or len(stripped) > 80:
            continue
        if line.strip().endswith((":", ":**")) or line.strip().startswith(("**", "#")):
            if re.fullmatch(
                r"(?:salary(?: range)?|compensation|pay(?: range| and benefits)?)\s*:?",
                stripped,
                re.I,
            ):
                return True
            # A location label such as "McLean, VA:" is still inside the
            # salary section. Only an actual new heading closes it.
            if line.strip().startswith(("**", "#")) or re.fullmatch(
                r"(?:qualifications|requirements|responsibilities|benefits|education)\s*:?",
                stripped,
                re.I,
            ):
                return False
    return False


def validated_salary(
    *,
    interval: str,
    minimum: Decimal | None,
    maximum: Decimal | None,
    currency: str,
    source: str,
) -> ParsedSalary | None:
    return _validated(interval, minimum, maximum, currency, source=source)


def _detect_interval(text: str, amount_span: tuple[int, int]) -> str | None:
    # A wage unit next to the amount takes priority over a different payroll
    # frequency elsewhere, e.g. "$25/hour, paid every two weeks".
    candidates = [
        (
            bool(
                re.search(
                    r"\b(?:paid|payroll|payments?)\b", text[amount_span[1] : match.start()], re.I
                )
            ),
            max(amount_span[0] - match.end(), match.start() - amount_span[1], 0),
            index,
            interval,
        )
        for index, (interval, pattern) in enumerate(_PERIOD_PATTERNS)
        for match in pattern.finditer(text)
        if max(amount_span[0] - match.end(), match.start() - amount_span[1], 0) <= 30
        or (
            interval == "hourly"
            and match.end() <= amount_span[0]
            and "hourly rate" in text[match.start() : amount_span[0]].lower()
        )
    ]
    return min(candidates)[3] if candidates else None


def _infer_interval(
    minimum: Decimal | None,
    maximum: Decimal | None,
) -> str | None:
    """Infer only unambiguous bare hourly/yearly amounts.

    Some US postings say only ``Pay Range: $14.50 - $30.51``. Values in a
    plausible hourly band are treated as hourly; values in an annual band are
    treated as yearly. Ambiguous daily/monthly-sized values remain unresolved
    instead of guessing their interval.
    """

    values = [value for value in (minimum, maximum) if value is not None]
    if values and all(Decimal("5") <= value <= Decimal("350") for value in values):
        return "hourly"
    if values and all(Decimal("5000") <= value <= Decimal("2000000") for value in values):
        return "yearly"
    return None


def _detect_currency(text: str, country_code: str | None) -> str:
    if re.search(r"(?<![A-Za-z_])(?:CA\$|C\$|CAD(?![A-Za-z_]))", text, re.I):
        return "CAD"
    if re.search(r"(?<![A-Za-z_])(?:US\$|USD(?![A-Za-z_]))", text, re.I):
        return "USD"
    return "CAD" if country_code == "CA" else "USD"


def _amount(value: str, suffix: str | None) -> Decimal:
    try:
        amount = Decimal(value.replace(",", ""))
    except InvalidOperation:
        return Decimal("0")
    return amount * ({"k": 1000, "m": 1000000}.get(suffix.lower(), 1) if suffix else 1)


def _validated(
    interval: str,
    minimum: Decimal | None,
    maximum: Decimal | None,
    currency: str,
    *,
    source: str = "description",
) -> ParsedSalary | None:
    if interval == "biweekly":
        minimum = minimum / 2 if minimum is not None else None
        maximum = maximum / 2 if maximum is not None else None
        interval = "weekly"
    if minimum is None and maximum is None:
        return None
    if minimum is not None and maximum is not None and minimum > maximum:
        return None
    if interval not in _LIMITS:
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
