from wecanfindintern.domain.recruiting_term import extract_recruiting_term_regex


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


def test_month_ranges_normalize_to_four_month_work_terms():
    from wecanfindintern.domain.recruiting_term import extract_recruiting_terms_regex

    cases = {
        "May to August 2027": [("summer", 2027)],
        "January through April 2027": [("winter", 2027)],
        "September–December 2027": [("fall", 2027)],
        "January–August 2027": [("winter", 2027), ("summer", 2027)],
        "January 4 to April 23, 2027": [("winter", 2027)],
        "September 2027–April 2028": [("fall", 2027), ("winter", 2028)],
    }
    for text, expected in cases.items():
        result = extract_recruiting_terms_regex(text, None)
        assert [(x.season, x.year) for x in result] == expected
        assert all(x.evidence in text for x in result)
    assert term("January–August 2027") == ("winter", 2027)


def test_month_only_start_dates_are_extracted():

    for text, expected in [
        ("Start Date:\nMay 2027", ("summer", 2027)),
        ("This position is starting January, 2027.", ("winter", 2027)),
        ("Job Assignment: May 2027 to August 2027", ("summer", 2027)),
    ]:
        result = extract_recruiting_term_regex("Engineering Intern", text)
        assert (result.season, result.year) == expected


def test_dates_for_deadlines_and_graduation_do_not_become_work_terms():
    for text in [
        "Application deadline: May 2027",
        "Expected graduation: January through April 2027",
        "Date: September 21, 2026",
        "The company was founded in January 2027.",
    ]:
        assert extract_recruiting_term_regex("Engineering Intern", text) is None


def test_primary_and_covered_terms_and_duration_are_separate():
    from wecanfindintern.domain.recruiting_term import normalize_recruiting_term

    result = normalize_recruiting_term("Engineering Intern", "Program Dates: January–August 2027")
    assert result.primary_term.display_name == "Winter 2027"
    assert [x.display_name for x in result.covered_terms] == ["Winter 2027", "Summer 2027"]
    assert not result.coverage_inferred
    result = normalize_recruiting_term("Intern", "Starting January 2027 for 8 months")
    assert result.primary_term.display_name == "Winter 2027"
    assert [x.display_name for x in result.covered_terms] == ["Winter 2027", "Summer 2027"]
    assert result.coverage_inferred
    assert (
        normalize_recruiting_term("June 2027, 12 weeks").primary_term.display_name == "Summer 2027"
    )


def test_abbreviated_full_dates_and_priority_over_title():
    from wecanfindintern.domain.recruiting_term import normalize_recruiting_term

    result = normalize_recruiting_term(
        "Summer 2027 Intern", "Internship period: Jan 4 – Apr 23, 2027"
    )
    assert result.primary_term.display_name == "Winter 2027"
    assert [x.display_name for x in result.covered_terms] == ["Winter 2027"]


def test_work_date_is_not_confused_with_other_fields():
    from wecanfindintern.domain.recruiting_term import normalize_recruiting_term

    result = normalize_recruiting_term(
        "Engineering Intern",
        "Apply by September 2026. Start Date: May 2027. Graduate by December 2028.",
    )
    assert result.primary_term.display_name == "Summer 2027"
    for text in [
        "Apply by May 2027",
        "Posted on May 2027",
        "Benefits effective May 2027",
        "2027 Internship",
        "Summer Internship",
    ]:
        assert normalize_recruiting_term("", text).primary_term is None
