"""Expand a scoped keyword catalog into executable collection plans."""

from __future__ import annotations

import re
from typing import Any

COUNTRY_INDEED_NAMES = {"CA": "Canada", "US": "USA"}


def expand_collection_catalog(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, dict) or "keyword_groups" not in raw:
        raise ValueError("collection config must be a plan list or keyword catalog")

    defaults = raw.get("defaults", {})
    sites = raw.get("sites", ["indeed", "linkedin"])
    locations = raw.get("locations", [])
    plans: list[dict[str, Any]] = []
    for group in raw["keyword_groups"]:
        domain = group["domain"]
        for keyword in group["keywords"]:
            for location in locations:
                country_code = location["country_code"].upper()
                if country_code not in COUNTRY_INDEED_NAMES:
                    raise ValueError(f"unsupported catalog country: {country_code}")
                plans.append(
                    {
                        "name": f"{country_code.lower()}-{_slug(domain)}-{_slug(keyword)}",
                        "enabled": True,
                        "sites": sites,
                        "query": {
                            "search_term": keyword,
                            "location": location,
                            "country_indeed": COUNTRY_INDEED_NAMES[country_code],
                            "distance": defaults.get("distance", 100),
                            "description_format": "markdown",
                            "linkedin_fetch_description": defaults.get(
                                "linkedin_fetch_description", True
                            ),
                            # WeCanFindIntern preserves the provider interval and performs
                            # its own validated annualization after deduplication.
                            "enforce_annual_salary": False,
                            "verbose": defaults.get("verbose", 1),
                        },
                        "interval_seconds": defaults.get("interval_seconds", 14_400),
                        "page_size": defaults.get("page_size", 25),
                        "max_results_per_source": defaults.get(
                            "max_results_per_source", 40
                        ),
                        "max_attempts": defaults.get("max_attempts", 3),
                    }
                )
    return plans


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
