"""Unit coverage for recommendation document metadata and hybrid fusion."""

from wecanfindintern.agent.recommend.documents import (
    DOCUMENT_VERSION,
    build_waterloo_document,
    infer_waterloo_opportunity_type,
)
from wecanfindintern.agent.recommend.repository import (
    RecommendationFilters,
    _document_filter_sql,
    _ranked_ids_with_lexical_floor,
)


def test_waterloo_opportunity_type_is_indexed():
    assert infer_waterloo_opportunity_type(["full_cycle"]) == "internship"
    assert infer_waterloo_opportunity_type(["graduating"]) == "full_time"
    document = build_waterloo_document(
        {
            "source_job_id": "WW-1",
            "title": "Developer",
            "organization": "Acme",
            "boards": ["employer_student_direct"],
        }
    )
    assert DOCUMENT_VERSION == "recommend-document.v2"
    assert document.metadata["opportunity_type"] == "internship"
    assert "Opportunity type: internship" in document.document_text


def test_retrieval_filters_are_parameterized_and_cover_eligibility_fields():
    sql, parameters = _document_filter_sql(
        "d",
        RecommendationFilters(
            target_roles=("backend", "server engineer"),
            locations=("Toronto",),
            work_modes=("remote",),
            opportunity_types=("internship",),
        ),
    )
    assert "d.title" in sql
    assert "metadata->>'location'" in sql
    assert "metadata->>'work_mode'" in sql
    assert "metadata->>'opportunity_type'" in sql
    assert "Toronto" not in sql
    assert parameters == [
        ["%backend%", "%server engineer%"],
        ["%toronto%"],
        ["remote"],
        ["internship"],
    ]


def test_hybrid_fusion_reserves_lexical_candidates():
    lexical = [
        {"source_job_id": "exact-1"},
        {"source_job_id": "exact-2"},
        {"source_job_id": "exact-3"},
    ]
    fused = {
        "semantic-1": 1.0,
        "semantic-2": 0.9,
        "semantic-3": 0.8,
        "exact-1": 0.1,
        "exact-2": 0.09,
        "exact-3": 0.08,
    }
    ranked = _ranked_ids_with_lexical_floor(
        lexical=lexical,
        fused=fused,
        excluded=set(),
        limit=4,
    )
    assert ranked[:2] == ["exact-1", "exact-2"]
    assert ranked[2:] == ["semantic-1", "semantic-2"]


def test_hybrid_fusion_backfills_excluded_lexical_floor_entries():
    lexical = [
        {"source_job_id": "tracked"},
        {"source_job_id": "exact-1"},
        {"source_job_id": "exact-2"},
    ]
    fused = {"semantic": 1.0, "exact-1": 0.1, "exact-2": 0.09}
    ranked = _ranked_ids_with_lexical_floor(
        lexical=lexical,
        fused=fused,
        excluded={"tracked"},
        limit=4,
    )
    assert ranked[:2] == ["exact-1", "exact-2"]
