from types import SimpleNamespace

from wecanfindintern.domain.jobs import structured_salary_range


def source_salary(*, interval, minimum, maximum, source):
    return SimpleNamespace(
        salary=SimpleNamespace(
            interval=interval,
            minimum=minimum,
            maximum=maximum,
            currency="USD",
            source=source,
        )
    )


def test_rejects_implausible_direct_yearly_salary() -> None:
    salary = structured_salary_range(
        source_salary(
            interval="yearly", minimum=24, maximum=29, source="direct_data"
        ).salary,
        country_code="US",
    )
    assert salary is None


def test_description_salary_always_uses_internal_extraction_pipeline() -> None:
    salary = structured_salary_range(
        source_salary(
            interval="hourly", minimum=24, maximum=29, source="description"
        ).salary,
        country_code="US",
    )
    assert salary is None
