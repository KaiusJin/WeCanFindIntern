"""Repository-level integration tests against a live PostgreSQL database."""

from __future__ import annotations

import pytest
from integration.conftest import seed_job

from wecanfindintern.agent.recommend.embeddings import EmbeddingConfig
from wecanfindintern.agent.recommend.repository import RecommendationRepository
from wecanfindintern.api.models import JobListFilters

pytestmark = pytest.mark.db


def test_geo_distribution_counts_by_region(database, run):
    seed_job(region_code="ON", country="CA")
    seed_job(region_code="ON", country="CA", title="Second Role")
    seed_job(region_code="NY", country="US", title="New York Role")

    from wecanfindintern.db.read_repository import JobReadRepository

    repo = JobReadRepository(database.pool)
    regions = run(repo.geo_distribution())

    by_code = {item["region_code"]: item for item in regions}
    assert by_code["ON"]["count"] == 2
    assert by_code["ON"]["region_name"] == "Ontario"
    assert by_code["NY"]["count"] == 1
    assert sum(item["count"] for item in regions) == 3


def test_embedding_readiness_accepts_global_and_source_scopes(database, run):
    repo = RecommendationRepository(database.pool)
    config = EmbeddingConfig(
        provider="Ollama",
        model="qwen3-embedding:0.6b",
        dimensions=768,
    )
    assert run(repo.has_embeddings(config)) is False
    assert run(repo.has_embeddings(config, source="public")) is False


def test_list_jobs_for_recommendation_excludes_tracked(database, run):
    kept = seed_job(title="Python Engineer", region_code="ON", country="CA")
    excluded = seed_job(title="Exclude Me", region_code="BC", country="CA")

    from uuid import UUID

    from wecanfindintern.db.read_repository import JobReadRepository

    repo = JobReadRepository(database.pool)
    rows = run(
        repo.list_jobs_for_recommendation(
            skills=["python"],
            exclude_public_ids=[UUID(excluded)],
            limit=10,
        )
    )

    returned = {str(row["item"].id) for row in rows}
    assert returned == {kept}


def test_recommendation_fallback_filters_before_limit(database, run):
    seed_job(title="Unrelated Python Role", region_code="NY", country="US")
    kept = seed_job(
        title="Backend Python Intern",
        region_code="ON",
        country="CA",
        work_mode="remote",
    )

    from wecanfindintern.db.read_repository import JobReadRepository

    repo = JobReadRepository(database.pool)
    rows = run(
        repo.list_jobs_for_recommendation(
            skills=["python"],
            exclude_public_ids=[],
            target_roles=["backend"],
            locations=["toronto"],
            work_modes=["remote"],
            limit=1,
        )
    )
    assert [str(row["item"].id) for row in rows] == [kept]


def test_list_jobs_filters_by_region(database, run):
    seed_job(region_code="ON", country="CA", title="Toronto Role")
    seed_job(region_code="NY", country="US", title="New York Role")

    from wecanfindintern.db.read_repository import JobReadRepository

    repo = JobReadRepository(database.pool)
    page = run(repo.list_jobs(JobListFilters(region="ON", limit=10)))

    assert page.total_count == 1
    assert page.items[0].title == "Toronto Role"
    assert page.items[0].location.region_code == "ON"


def test_list_jobs_filters_multiple_regions_and_work_modes(database, run):
    seed_job(region_code="ON", country="CA", title="Toronto Role", work_mode="hybrid")
    seed_job(region_code="NY", country="US", title="New York Role", work_mode="remote")
    seed_job(region_code="BC", country="CA", title="Vancouver Role", work_mode="onsite")

    from wecanfindintern.db.read_repository import JobReadRepository

    repo = JobReadRepository(database.pool)
    page = run(
        repo.list_jobs(
            JobListFilters(
                regions=["ON,CA", "NY,US"],
                work_modes=["hybrid", "remote"],
                limit=10,
            )
        )
    )

    assert {item.title for item in page.items} == {"Toronto Role", "New York Role"}


def test_list_jobs_relevance_sort_and_cursor_are_stable(database, run):
    strong = seed_job(
        title="Python Backend Engineer",
        company="Python Backend Systems",
        description="Build Python backend services.",
    )
    weak = seed_job(
        title="Backend Python Developer",
        description="Maintain services using Python.",
    )

    from wecanfindintern.db.read_repository import JobReadRepository

    repo = JobReadRepository(database.pool)
    first = run(
        repo.list_jobs(
            JobListFilters(
                query="python backend",
                sort_by_relevance=True,
                limit=1,
            )
        )
    )
    assert str(first.items[0].id) == strong
    assert first.total_count == 2
    assert first.has_more is True
    assert first.next_cursor

    second = run(
        repo.list_jobs(
            JobListFilters(
                query="python backend",
                sort_by_relevance=True,
                cursor=first.next_cursor,
                limit=1,
            )
        )
    )
    assert [str(item.id) for item in second.items] == [weak]
    assert second.has_more is False
