from wecanfindintern.domain.recruiting_term import extract_recruiting_term_regex
from wecanfindintern.domain.recruiting_term_llm import (
    _has_conflicting_month_seasons,
    _normalize,
)


def term(value: str) -> tuple[str, int] | None:
    result = extract_recruiting_term_regex(value, None)
    return (result.season, result.year) if result else None


def test_normalizes_common_recruiting_term_expressions() -> None:
    assert term("Software Intern - Winter 2027") == ("winter", 2027)
    assert term("2027 Spring Term Data Co-op") == ("spring", 2027)
    assert term("Summer Internship Program '27") == ("summer", 2027)
    assert term("Autumn 2028 ML Placement") == ("fall", 2028)
    assert term("Backend Intern WI27") == ("winter", 2027)
    assert term("AI Intern 2027SU") == ("summer", 2027)
    assert term("Security Co-op F27") == ("fall", 2027)


def test_title_takes_precedence_over_description() -> None:
    result = extract_recruiting_term_regex(
        "Software Developer - Summer 2027",
        "Our Winter 2026 program has already closed.",
    )
    assert result is not None
    assert (result.season, result.year, result.source) == (
        "summer",
        2027,
        "regex_title",
    )


def test_does_not_force_ambiguous_or_missing_term() -> None:
    assert term("Internship Spring/Summer 2027") is None
    assert term("Fall 2026 or Winter 2027 Internship") is None
    assert term("Generic Software Intern") is None


def test_llm_evidence_normalization_ignores_markdown_punctuation() -> None:
    assert _normalize("Term start: January 2027") in _normalize(
        "* **Term start** : January 2027"
    )


def test_conflicting_start_months_do_not_force_one_season() -> None:
    assert _has_conflicting_month_seasons("Term start: January or May 2027")
    assert not _has_conflicting_month_seasons("Term start: January 2027")


def test_job_list_filters_recruiting_term_parsing() -> None:
    from wecanfindintern.api.models import JobListFilters

    filters1 = JobListFilters(recruiting_term="Summer 2027")
    assert filters1.season == "summer"
    assert filters1.recruiting_year == 2027

    filters2 = JobListFilters(season="FALL", recruiting_year=2026)
    assert filters2.season == "fall"
    assert filters2.recruiting_year == 2026

    filters3 = JobListFilters(recruiting_term="winter_2028")
    assert filters3.season == "winter"
    assert filters3.recruiting_year == 2028


def test_job_list_filters_hourly_salary() -> None:
    from decimal import Decimal

    from wecanfindintern.api.models import JobListFilters

    filters = JobListFilters(hourly_salary_min=Decimal("25.50"), hourly_salary_max=Decimal("50.00"))
    assert filters.hourly_salary_min == Decimal("25.50")
    assert filters.hourly_salary_max == Decimal("50.00")


def test_job_list_filters_normalize_multiple_facet_values() -> None:
    from wecanfindintern.api.models import JobListFilters

    filters = JobListFilters(
        countries=["ca", "US", "ca"],
        regions=["on,ca", "ny,us"],
        cities=["Toronto", "New York"],
        work_modes=["remote", "hybrid"],
        opportunity_types=["internship", "co_op"],
        schedule_types=["full_time", "part_time"],
        categories=["software_development", "data_analytics"],
        skills=["python", "react"],
        recruiting_terms=["Summer 2027", "Fall 2026"],
    )

    assert filters.countries == ["CA", "US"]
    assert filters.regions == ["ON,CA", "NY,US"]
    assert filters.work_modes == ["remote", "hybrid"]
    assert filters.recruiting_terms == ["Summer 2027", "Fall 2026"]
