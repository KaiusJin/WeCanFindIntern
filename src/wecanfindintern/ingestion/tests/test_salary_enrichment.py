import asyncio
from types import SimpleNamespace

from wecanfindintern.db.repositories.salary import SalaryEnrichmentCandidate
from wecanfindintern.domain.jobs import structured_salary_range
from wecanfindintern.ingestion import salary_enrichment
from wecanfindintern.ingestion.salary_llm import SalaryLLMCall


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

    calls = []

    def llm_call(*_args, **_kwargs):
        calls.append(True)
        return SalaryLLMCall(None, "test-model")

    repository = FakeRepository()
    monkeypatch.setattr(salary_enrichment, "extract_salary_with_deepseek_call", llm_call)
    stats = asyncio.run(
        salary_enrichment.enrich_missing_salaries(
            repository,
            [SimpleNamespace(source_fingerprint="aa" * 32)],
        )
    )

    assert stats.llm == 0
    assert len(calls) == 1
    assert [(check["job_id"], check["status"]) for check in repository.checks] == [
        (1, "not_found"),
        (2, "not_found"),
    ]
