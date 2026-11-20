from decimal import Decimal

import pytest

from wecanfindintern.domain.normalization import annualize_salary
from wecanfindintern.domain.salary import (
    extract_salary_from_description,
)


@pytest.mark.parametrize(
    "text,currency,minimum,maximum,interval",
    [
        ("$25–35 CAD/hour", "CAD", "25", "35", "hourly"),
        ("$80k–100k annually", "USD", "80000", "100000", "yearly"),
        ("$1,500–2,000 weekly", "USD", "1500", "2000", "weekly"),
        ("USD $21.00 - USD $24.00 /Hr.", "USD", "21", "24", "hourly"),
        ("CAD25–35/hour", "CAD", "25", "35", "hourly"),
        ("$200–300 daily", "USD", "200", "300", "daily"),
        ("$3,000–4,000 monthly", "USD", "3000", "4000", "monthly"),
        ("$2,000–3,000 biweekly", "USD", "1000", "1500", "weekly"),
        ("$2,000–3,000 every two weeks", "USD", "1000", "1500", "weekly"),
        ("$2,000–3,000 every 2 weeks", "USD", "1000", "1500", "weekly"),
        ("$2,000–3,000 fortnightly", "USD", "1000", "1500", "weekly"),
        ("$2,000–3,000 bi-weekly", "USD", "1000", "1500", "weekly"),
        ("$2,000–3,000 每两周", "USD", "1000", "1500", "weekly"),
        ("$25/hour, paid every two weeks", "USD", "25", None, "hourly"),
        ("Annual salary $52,000, paid biweekly", "USD", "52000", None, "yearly"),
    ],
)
def test_salary_values_and_units(text, currency, minimum, maximum, interval):
    result = extract_salary_from_description(text)
    assert result is not None
    assert (result.currency, result.minimum, result.maximum, result.interval) == (
        currency,
        Decimal(minimum),
        Decimal(maximum) if maximum else None,
        interval,
    )


@pytest.mark.parametrize(
    "text",
    [
        "AutoCAD academic cadence entrepreneurial",
        "CADENCE",
        "USD_TOKEN",
        "GBP EUR £ €",
    ],
)
def test_currency_words_do_not_trigger_unrelated_context(text):
    from wecanfindintern.domain.salary import salary_candidates

    assert salary_candidates(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "Competitive salary",
        "Salary £50,000–60,000 annually",
        "Salary EUR 50,000–60,000 annually",
        "Salary 50,000–60,000 GBP annually",
        "Salary ¥50,000–60,000 annually",
    ],
)
def test_unknown_and_unsupported_pay_is_not_invented(text):
    assert extract_salary_from_description(text) is None


def test_selects_one_explicit_currency_without_conversion():
    text = "CAD 55/hr, USD 60/hr"
    us = extract_salary_from_description(text, country_code="US")
    ca = extract_salary_from_description(text, country_code="CA")
    assert (us.currency, us.minimum) == ("USD", Decimal("60"))
    assert (ca.currency, ca.minimum) == ("CAD", Decimal("55"))
    assert extract_salary_from_description(text) is None


def test_biweekly_output_is_converted_once_and_annualizes_consistently():
    result = extract_salary_from_description("The range is $1,635.00 - $3,185.00 biweekly")
    assert result.interval == "weekly"
    assert result.minimum == Decimal("817.50")
    assert result.maximum == Decimal("1592.50")
    assert annualize_salary(result.minimum, "weekly") == Decimal("42510.00")


def test_amount_first_candidates_do_not_turn_plain_numbers_into_pay():
    from wecanfindintern.domain.salary import salary_candidates

    assert salary_candidates("Graduate in 2027; work 40 hours; GPA 3.0; 3–5 years experience") == []
    assert (
        extract_salary_from_description("Our team has 1,234 people and serves 85k customers.")
        is None
    )
    result = extract_salary_from_description("Salary range: 80,000–100,000 annually")
    assert (result.minimum, result.maximum, result.interval) == (
        Decimal("80000"),
        Decimal("100000"),
        "yearly",
    )


def test_bonus_allowance_and_placeholder_are_classified_but_not_selected():
    from wecanfindintern.domain.salary import salary_candidates

    text = (
        "Full-time, paid co-op $25.00-50.00 USD/hour\n"
        "Signing bonus: $5,000\nHousing stipend: $2,000\nPay Range: $0 - $0"
    )
    candidates = salary_candidates(text)
    assert {c.category for c in candidates} >= {"base", "bonus", "allowance", "placeholder"}
    result = extract_salary_from_description(text)
    assert (result.minimum, result.maximum, result.interval) == (
        Decimal("25"),
        Decimal("50"),
        "hourly",
    )


def test_non_salary_numbers_and_neighbouring_requirements_do_not_replace_base_pay():
    text = (
        "Minimum 3.0 GPA; 3–5 years experience.\n"
        "Compensation: $25–35/hour.\nCompany revenue: $80m/year.\n401(k) match."
    )
    result = extract_salary_from_description(text)
    assert (result.minimum, result.maximum, result.interval) == (
        Decimal("25"),
        Decimal("35"),
        "hourly",
    )


def test_local_evidence_is_required_for_distant_numbers():
    assert extract_salary_from_description("Salary " + "x" * 110 + "$80,000") is None
    assert extract_salary_from_description("$1,500–2,000 per week").interval == "weekly"
    assert extract_salary_from_description("hourly rate " + " " * 35 + "$25").interval == "hourly"


def test_salary_section_intro_covers_location_specific_rates():
    result = extract_salary_from_description(
        "The minimum and maximum full-time annual salaries for this role are listed below. "
        "These figures apply only to candidates hired in these locations. "
        "Part-time roles are prorated based on agreed working hours.\n"
        "McLean, VA: $176,000 - $176,000\nNew York, NY: $192,000 - $192,000",
        country_code="US",
    )
    assert result.minimum == result.maximum == Decimal("176000")
    assert result.interval == "yearly"


def test_sentence_punctuation_does_not_hide_an_amount():
    result = extract_salary_from_description("The hourly rate is $55.", country_code="US")
    assert result.minimum == Decimal("55")
    assert result.interval == "hourly"
