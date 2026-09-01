"""Unit tests for collection plan catalog expansion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wecanfindintern.ingestion.collection_catalog import expand_collection_catalog


def test_default_campaign_only_enables_indeed_and_linkedin() -> None:
    config_path = Path(__file__).parents[1] / "config" / "collection_plans.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert config["sites"] == ["indeed", "linkedin"]
    assert "source_overrides" not in config


def test_expands_sites_from_config() -> None:
    raw = {
        "sites": ["indeed", "google"],
        "locations": [{"country_code": "CA"}],
        "keyword_groups": [{"domain": "x", "keywords": ["intern"]}],
        "source_overrides": {"google": {"google_search_term": "{search_term} near {location}"}},
    }
    plans = expand_collection_catalog(raw)
    assert len(plans) == 1
    assert plans[0]["sites"] == ["indeed", "google"]
    overrides = plans[0]["query"]["source_overrides"]
    assert overrides["google"]["google_search_term"] == "{search_term} near {location}"


def test_google_requires_google_search_term_override() -> None:
    raw = {
        "sites": ["indeed", "google"],
        "locations": [{"country_code": "US"}],
        "keyword_groups": [{"domain": "x", "keywords": ["intern"]}],
    }
    with pytest.raises(ValueError, match="google_search_term"):
        expand_collection_catalog(raw)


def test_default_sites_and_unsupported_country() -> None:
    plans = expand_collection_catalog(
        {
            "locations": [{"country_code": "CA"}],
            "keyword_groups": [{"domain": "x", "keywords": ["intern"]}],
        }
    )
    assert plans[0]["sites"] == ["indeed", "linkedin"]
    with pytest.raises(ValueError, match="unsupported catalog country"):
        expand_collection_catalog(
            {
                "locations": [{"country_code": "IN"}],
                "keyword_groups": [{"domain": "x", "keywords": ["intern"]}],
            }
        )
