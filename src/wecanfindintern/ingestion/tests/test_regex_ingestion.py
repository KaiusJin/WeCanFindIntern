import asyncio
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from wecanfindintern.domain.recruiting_term import recruiting_term_input_hash
from wecanfindintern.ingestion import pipeline, recruiting_term_enrichment, salary_enrichment
from wecanfindintern.llm import gateway


def forbid_llm(**_kwargs):
    raise AssertionError("Collection enrichment must never call an LLM")


def test_recruiting_terms_cache_matches_and_unknown_without_llm(monkeypatch):
    monkeypatch.setattr(gateway, "complete_json", forbid_llm)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "configured-but-not-used")
    cached_title = "Winter 2027 Intern"
    candidates = [
        SimpleNamespace(
            job_id=1,
            title=cached_title,
            description=None,
            checked_input_hash=recruiting_term_input_hash(cached_title, None),
        ),
        SimpleNamespace(
            job_id=2,
            title="Engineering Intern",
            description="Start Date: May 2027",
            checked_input_hash=None,
        ),
        SimpleNamespace(
            job_id=3,
            title="Engineering Intern",
            description="Summer experience; graduate in 2027.",
            checked_input_hash=None,
        ),
        SimpleNamespace(
            job_id=4, title="Generic role", description="No dates.", checked_input_hash=None
        ),
    ]

    class Repository:
        def __init__(self):
            self.saved = []

        async def recruiting_term_candidates(self, fingerprints):
            assert fingerprints == ["one-source"]
            return candidates

        async def persist_recruiting_term(self, **kwargs):
            self.saved.append(kwargs)
            return True

    repo = Repository()
    stats = asyncio.run(recruiting_term_enrichment.enrich_recruiting_terms(repo, ["one-source"]))
    assert (stats.regex, stats.not_found, stats.skipped_cached, stats.failed) == (1, 2, 1, 0)
    assert [x["job_id"] for x in repo.saved] == [2, 3, 4]
    assert repo.saved[0]["term"].display_name == "Summer 2027"
    assert repo.saved[1]["term"] is repo.saved[2]["term"] is None
    assert all("model" not in row for row in repo.saved)


def test_changed_jd_write_is_not_counted_as_success_or_cached_miss(monkeypatch):
    monkeypatch.setattr(gateway, "complete_json", forbid_llm)

    class Repository:
        async def salary_enrichment_candidates(self, _fingerprints):
            return [
                SimpleNamespace(
                    job_id=1,
                    description="Pay $25–35/hour",
                    country_code="US",
                    description_hash="aa" * 32,
                )
            ]

        async def persist_enriched_salary(self, **_kwargs):
            return False

        async def persist_enrichment_check(self, **_kwargs):
            raise AssertionError("A raced write is not a definitive salary miss")

    stats = asyncio.run(
        salary_enrichment.enrich_missing_salaries(
            Repository(),
            [SimpleNamespace(source_fingerprint="aa" * 32)],
        )
    )
    assert (stats.regex, stats.not_found, stats.failed) == (0, 0, 1)


def test_ingestion_finishes_deduplication_before_regex_and_never_calls_llm(monkeypatch):
    monkeypatch.setattr(gateway, "complete_json", forbid_llm)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "configured-but-not-used")
    events = []
    saved_salaries = []
    saved_terms = []
    original_salary = salary_enrichment.extract_salary_from_description
    original_term = recruiting_term_enrichment.extract_recruiting_term_regex

    def salary_regex(*args, **kwargs):
        assert events[:2] == ["dedupe", "dedupe"]
        events.append("salary-regex")
        return original_salary(*args, **kwargs)

    def term_regex(*args, **kwargs):
        assert events[:2] == ["dedupe", "dedupe"]
        events.append("term-regex")
        return original_term(*args, **kwargs)

    class Jobs:
        def __init__(self, pool, timings=None):
            pass

        async def ingest_batch(self, **kwargs):
            events.append("dedupe")
            return Counter(created=1)

    class Salaries:
        def __init__(self, pool):
            pass

        async def salary_enrichment_candidates(self, fingerprints):
            assert len(fingerprints) == 2
            # Repository resolves the merged records to one unique canonical JD.
            return [
                SimpleNamespace(
                    job_id=7,
                    description="Pay $2,000–3,000 biweekly",
                    country_code="CA",
                    description_hash="bb" * 32,
                )
            ]

        async def persist_enriched_salary(self, **kwargs):
            saved_salaries.append(kwargs)
            return True

    class Terms:
        def __init__(self, pool):
            pass

        async def recruiting_term_candidates(self, fingerprints):
            return [
                SimpleNamespace(
                    job_id=7,
                    title="Engineering Intern",
                    description="Program Dates: January–August 2027",
                    checked_input_hash=None,
                )
            ]

        async def persist_recruiting_term(self, **kwargs):
            saved_terms.append(kwargs)
            return True

    monkeypatch.setattr(pipeline, "JobIngestionRepository", Jobs)
    monkeypatch.setattr(pipeline, "SalaryRepository", Salaries)
    monkeypatch.setattr(pipeline, "RecruitingTermRepository", Terms)
    monkeypatch.setattr(pipeline, "canonical_job_from_normalized", lambda job, **kwargs: job)
    monkeypatch.setattr(salary_enrichment, "extract_salary_from_description", salary_regex)
    monkeypatch.setattr(recruiting_term_enrichment, "extract_recruiting_term_regex", term_regex)
    report = asyncio.run(
        pipeline.run_ingestion_pipeline(
            pool=object(),
            run_id=1,
            scraped_at=datetime.now(UTC),
            batch_size=1,
            jobs=[
                SimpleNamespace(source_fingerprint="aa" * 32, salary=None),
                SimpleNamespace(source_fingerprint="bb" * 32, salary=None),
            ],
        )
    )
    assert events == ["dedupe", "dedupe", "salary-regex", "term-regex"]
    assert (report.salary.regex, report.recruiting_term.regex) == (1, 1)
    salary = saved_salaries[0]["salary"]
    assert (salary.currency, salary.interval, salary.minimum, salary.maximum) == (
        "CAD",
        "weekly",
        Decimal("1000"),
        Decimal("1500"),
    )
    assert salary.annualized_minimum == Decimal("52000")
    assert saved_terms[0]["term"].display_name == "Winter 2027"
    assert "model" not in saved_salaries[0] and "model" not in saved_terms[0]


def test_regex_repository_writes_keep_guards_and_clear_model_metadata():
    from contextlib import asynccontextmanager

    from wecanfindintern.db.repositories.recruiting_term import RecruitingTermRepository
    from wecanfindintern.db.repositories.salary import SalaryRepository

    writes = []

    class Connection:
        async def execute(self, query, parameters):
            assert query.count("%s") == len(parameters)
            writes.append((query, parameters))
            return SimpleNamespace(rowcount=1)

    class Pool:
        @asynccontextmanager
        async def connection(self):
            yield Connection()

    async def persist():
        salary = salary_enrichment._salary_range(
            salary_enrichment.extract_salary_from_description("Pay $25–35/hour"),
        )
        assert await SalaryRepository(Pool()).persist_enriched_salary(
            job_id=7,
            description_hash="bb" * 32,
            salary=salary,
        )
        assert await SalaryRepository(Pool()).persist_enrichment_check(
            job_id=8,
            description_hash="cc" * 32,
            status="not_found",
        )
        assert await RecruitingTermRepository(Pool()).persist_recruiting_term(
            job_id=7,
            input_hash="dd" * 32,
            term=recruiting_term_enrichment.extract_recruiting_term_regex(
                "Winter 2027 Intern", None
            ),
        )

    asyncio.run(persist())
    assert len(writes) == 3
    assert all("model = NULL" in query for query, _ in writes)
    assert all("WHERE id = %s" in query for query, _ in writes)
    assert "description_hash = %s" in writes[0][0]
    assert "salary_min IS NULL" in writes[0][0]
    assert "digest(title" in writes[2][0]
