"""Tests for deterministic location parsing and display-name composition."""

from wecanfindintern.db.read_repository import location_display_name
from wecanfindintern.domain.location import clean_location_display, parse_location


def test_canadian_city_full_form():
    location = parse_location("Toronto, Ontario, Canada")
    assert location.city == "Toronto"
    assert location.region_code == "ON"
    assert location.region_name == "Ontario"
    assert location.region_type == "province"
    assert location.country_code == "CA"
    assert location.country_name == "Canada"


def test_canadian_city_code_form():
    location = parse_location("Toronto, ON, CA")
    assert location.city == "Toronto"
    assert location.region_code == "ON"
    assert location.region_name == "Ontario"
    assert location.country_code == "CA"


def test_ontario_typos_are_normalized():
    for typo in ("Waterloo, Ontraio, Canada", "Toronto, Ontairo"):
        location = parse_location(typo)
        assert location.region_code == "ON", typo


def test_us_city_without_country():
    location = parse_location("New York, NY")
    assert location.city == "New York"
    assert location.region_code == "NY"
    assert location.region_name == "New York"
    assert location.country_code == "US"
    assert location.country_name == "United States"


def test_remote_with_country_has_no_city_or_region():
    location = parse_location("Remote, US")
    assert location.city is None
    assert location.region_code is None
    assert location.region_name is None
    assert location.country_code == "US"
    assert location.country_name == "United States"
    # Dedupe key semantics are preserved for remote postings.
    assert location.normalized == "remote|us"


def test_remote_alone_is_fully_remote():
    location = parse_location("Work from home")
    assert location.city is None
    assert location.region_code is None
    assert location.country_code is None
    assert location.normalized == "remote"


def test_area_phrase_falls_back_to_city_slot():
    location = parse_location("Greater Toronto Area, Canada")
    assert location.city == "Greater Toronto Area"
    assert location.region_code is None
    assert location.region_name is None
    assert location.country_code == "CA"


def test_puerto_rico_is_a_us_territory():
    location = parse_location("San Juan, PR, US")
    assert location.city == "San Juan"
    assert location.region_code == "PR"
    assert location.region_name == "Puerto Rico"
    assert location.region_type == "territory"
    assert location.country_code == "US"


def test_montreal_is_accented_for_quebec():
    location = parse_location("Montreal, Quebec, Canada")
    assert location.city == "Montréal"
    assert location.region_code == "QC"


def test_country_only_row():
    location = parse_location("United States")
    assert location.city is None
    assert location.region_code is None
    assert location.country_code == "US"
    assert location.country_name == "United States"


def test_unrecognized_international_location_is_untouched():
    location = parse_location("London, England")
    assert location.city == "London"
    assert location.country_code is None


def _row(**overrides):
    base = {
        "location_text": "Toronto, ON, CA",
        "city": "Toronto",
        "region_code": "ON",
        "region_name": "Ontario",
        "region_type": "province",
        "country_code": "CA",
        "country_name": "Canada",
    }
    base.update(overrides)
    return base


def test_display_name_prefers_cleaned_hierarchy_over_raw_text():
    # Same place, four raw spellings — one display form.
    for text in ("Toronto, Ontario, Canada", "Toronto, ON, CA", "toronto on"):
        display = location_display_name(_row(location_text=text))
        assert display == "Toronto, Ontario, Canada", text


def test_display_name_uses_region_code_when_name_missing():
    display = location_display_name(_row(region_name=None))
    assert display == "Toronto, ON, Canada"


def test_display_name_country_only_row_shows_country_name():
    display = location_display_name(
        _row(
            location_text="US",
            city=None,
            region_code=None,
            region_name=None,
            country_code="US",
            country_name="United States",
        )
    )
    assert display == "United States"


def test_display_name_falls_back_to_location_text():
    display = location_display_name(
        _row(
            city=None,
            region_code=None,
            region_name=None,
            country_code=None,
            country_name=None,
            location_text="Somewhere, Overseas",
        )
    )
    assert display == "Somewhere, Overseas"


def test_clean_location_display_uses_full_canonical_names():
    assert clean_location_display("Toronto, ON, CA") == "Toronto, Ontario, Canada"
    assert clean_location_display("New York, NY") == "New York, New York, United States"
    assert clean_location_display("Remote, US") == "Remote, United States"


def test_clean_location_display_preserves_unknown_international_hierarchy():
    assert clean_location_display("London, England") == "London, England"
