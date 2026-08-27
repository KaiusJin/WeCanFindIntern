from types import SimpleNamespace

from wecanfindintern.ingestion.salary_enrichment import _structured_salary


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
    salary = _structured_salary(
        [source_salary(interval="yearly", minimum=24, maximum=29, source="direct_data")],
        country_code="US",
    )
    assert salary is None


def test_description_salary_always_uses_internal_extraction_pipeline() -> None:
    salary = _structured_salary(
        [source_salary(interval="hourly", minimum=24, maximum=29, source="description")],
        country_code="US",
    )
    assert salary is None
