import asyncio
from types import SimpleNamespace

from cloud.pipeline import salary as salary_enrichment
from cloud.storage.salary import SalaryEnrichmentCandidate
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
        source_salary(interval="yearly", minimum=24, maximum=29, source="direct_data").salary,
        country_code="US",
    )
    assert salary is None


def test_description_salary_always_uses_internal_extraction_pipeline() -> None:
    salary = structured_salary_range(
        source_salary(interval="hourly", minimum=24, maximum=29, source="description").salary,
        country_code="US",
    )
    assert salary is None


def test_salary_enrichment_caches_definitive_misses(monkeypatch) -> None:
    candidates = [
        SalaryEnrichmentCandidate(
            job_id=1,
            title="Software Intern",
            description="Build APIs and tests.",
            description_hash="11" * 32,
            country_code="CA",
            source_fingerprints=["aa" * 32],
            checked_description_hash=None,
            enrichment_status=None,
        ),
        SalaryEnrichmentCandidate(
            job_id=2,
            title="Data Intern",
            description="Compensation is competitive and depends on experience.",
            description_hash="22" * 32,
            country_code="US",
            source_fingerprints=["bb" * 32],
            checked_description_hash=None,
            enrichment_status=None,
        ),
    ]

    class FakeRepository:
        def __init__(self):
            self.checks = []

        async def salary_enrichment_candidates(self, _fingerprints):
            return list(candidates)

        async def persist_enriched_salary(self, **_kwargs):
            raise AssertionError("no salary should be persisted")

        async def persist_enrichment_check(self, **kwargs):
            self.checks.append(kwargs)
            return True

    repository = FakeRepository()
    stats = asyncio.run(
        salary_enrichment.enrich_missing_salaries(
            repository,
            [SimpleNamespace(source_fingerprint="aa" * 32)],
        )
    )

    assert stats.regex == 0
    assert stats.not_found == 2
    assert [(check["job_id"], check["status"]) for check in repository.checks] == [
        (1, "not_found"),
        (2, "not_found"),
    ]
