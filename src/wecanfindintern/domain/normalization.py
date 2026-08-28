"""Deterministic text/company/salary normalization helpers.

This module is intentionally free of IO and pydantic contracts so it can be
reused by both the domain layer and the ingestion boundary.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import UTC, datetime
from decimal import Decimal

EMPLOYMENT_TYPE_MAP = {
    "fulltime": "full_time",
    "full time": "full_time",
    "parttime": "part_time",
    "part time": "part_time",
    "internship": "internship",
    "intern": "internship",
    "contract": "contract",
    "temporary": "temporary",
}

COMPANY_SUFFIXES = re.compile(
    r"\b(?:incorporated|inc|limited|ltd|corporation|corp|company|co|llc|plc)\.?$"
)

SALARY_ANNUALIZATION_FACTORS = {
    "hourly": Decimal("2080"),
    "daily": Decimal("260"),
    "weekly": Decimal("52"),
    "monthly": Decimal("12"),
    "yearly": Decimal("1"),
    "annual": Decimal("1"),
}


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def normalize_job_description(value: str | None) -> str | None:
    """Collapse blank lines to one newline while preserving JD line structure."""

    if not value:
        return None
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    lines = (
        re.sub(r"[^\S\n]+", " ", line, flags=re.UNICODE).strip()
        for line in normalized.split("\n")
    )
    normalized = "\n".join(line for line in lines if line)
    return normalized or None


def normalize_company(value: str | None) -> str:
    normalized = normalize_text(value)
    previous = None
    while normalized and normalized != previous:
        previous = normalized
        normalized = COMPANY_SUFFIXES.sub("", normalized).strip()
    return normalized


def normalize_title(value: str) -> str:
    normalized = normalize_text(value)
    tokens = normalized.split()
    expansions = {
        "swe": ["software", "engineer"],
        "sr": ["senior"],
        "jr": ["junior"],
        "dev": ["developer"],
    }
    expanded: list[str] = []
    for token in tokens:
        expanded.extend(expansions.get(token, [token]))
    return " ".join(expanded)


def normalize_employment_types(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        normalized_value = normalize_text(value)
        mapped = EMPLOYMENT_TYPE_MAP.get(
            normalized_value,
            normalized_value.replace(" ", "_"),
        )
        if mapped and mapped not in normalized:
            normalized.append(mapped)
    return normalized


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def to_decimal(value: float | None) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def annualize_salary(value: Decimal | None, interval: str | None) -> Decimal | None:
    if value is None or not interval:
        return None
    factor = SALARY_ANNUALIZATION_FACTORS.get(normalize_text(interval))
    return (value * factor).quantize(Decimal("0.01")) if factor else None


def default_salary_currency(country_code: str | None) -> str:
    return {
        "CA": "CAD",
        "US": "USD",
        "GB": "GBP",
    }.get(country_code or "", "USD")


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
