"""Unit tests for the deterministic recommendation scorer and LLM re-ranker."""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from wecanfindintern.agent.recommend.rerank import rerank_with_llm
from wecanfindintern.agent.recommend.scoring import (
    enforce_company_diversity,
    expand_skill_tags,
    expand_skill_terms,
    expand_target_roles,
    is_expired,
    score_candidate,
    target_role_matches,
)
from wecanfindintern.agent.tools import LlmConfig
from wecanfindintern.llm.gateway import LLMError

TODAY = date(2026, 8, 29)


def test_word_boundary_prevents_substring_false_positives():
    terms = expand_skill_terms({"go"})
    assert "go" in terms
    scored = score_candidate(
        {"go"},
        {
            "title": "Google Cloud Intern",
            "skill_tags": ["google cloud"],
            "description": "Support Google Cloud customers.",
        },
        today=TODAY,
    )
    assert scored.matched_skills == []
    assert scored.score == 0

    matched = score_candidate(
        {"go"},
        {
            "title": "Go Developer",
            "skill_tags": [],
            "description": "Build services in Go.",
        },
        today=TODAY,
    )
    assert "go" in matched.matched_skills


def test_cplusplus_style_terms_match_with_custom_boundaries():
    scored = score_candidate(
        {"c++"},
        {"title": "C++ Engine Developer", "skill_tags": [], "description": None},
        today=TODAY,
    )
    assert "c++" in scored.matched_skills


def test_alias_expansion_matches_either_side():
    assert expand_skill_terms({"js"}) == {"js", "javascript"}
    tags = expand_skill_tags(["JavaScript"])
    assert "js" in tags
    scored = score_candidate(
        {"JavaScript"},
        {"title": "Frontend Dev", "skill_tags": ["js"], "description": None},
        today=TODAY,
    )
    assert scored.signals["components"]["skill_tags"] > 0


def test_component_caps_bound_long_skill_lists():
    many_tags = [f"skill{i}" for i in range(20)]
    profile_skills = {f"skill{i}" for i in range(20)}
    scored = score_candidate(
        profile_skills,
        {"title": "", "skill_tags": many_tags, "description": ""},
        today=TODAY,
    )
    assert scored.signals["components"]["skill_tags"] == 30  # MAX_SKILL_TAG_SCORE


def test_freshness_and_deadline_bonuses():
    fresh = score_candidate(
        set(),
        {
            "title": "",
            "skill_tags": [],
            "date_posted": datetime(2026, 8, 27, tzinfo=UTC).isoformat(),
        },
        today=TODAY,
    )
    assert fresh.signals["freshness"] == "new_this_week"
    assert fresh.signals["components"]["freshness"] == 5

    urgent = score_candidate(
        set(),
        {
            "title": "",
            "skill_tags": [],
            "application_deadline": date(2026, 9, 5).isoformat(),
        },
        today=TODAY,
    )
    assert urgent.signals["deadline"] == "closing_soon"

    stale = score_candidate(
        set(),
        {"title": "", "skill_tags": [], "date_posted": "2026-01-01"},
        today=TODAY,
    )
    assert "freshness" not in stale.signals


def test_preference_components_apply():
    scored = score_candidate(
        set(),
        {
            "title": "Backend Developer Intern",
            "location_text": "Waterloo, ON, Canada",
            "work_mode": "remote",
            "skill_tags": [],
        },
        preferences={
            "TARGET_ROLES": "backend developer",
            "TARGET_LOCATIONS": "waterloo",
            "WORK_MODE": "REMOTE",
        },
        today=TODAY,
    )
    components = scored.signals["components"]
    assert components["role_preference"] == 12
    assert components["location_preference"] == 6
    assert components["work_mode_preference"] == 5


def test_role_aliases_match_without_loose_substrings():
    assert "platform engineer" in expand_target_roles(["devops"])
    assert target_role_matches("Site Reliability Engineer Intern", ["devops"])
    assert not target_role_matches("Sales Representative", ["devops"])


def test_early_career_scoring_promotes_internships_and_penalizes_senior_roles():
    internship = score_candidate(
        {"python"},
        {
            "title": "Software Engineer Intern",
            "opportunity_type": "internship",
            "skill_tags": ["python"],
        },
        early_career=True,
        today=TODAY,
    )
    senior = score_candidate(
        {"python"},
        {
            "title": "Senior Software Engineer",
            "opportunity_type": "full_time",
            "skill_tags": ["python"],
        },
        early_career=True,
        today=TODAY,
    )
    assert internship.score > senior.score
    assert internship.signals["components"]["opportunity_fit"] == 12
    assert senior.signals["penalties"]["senior_role"] == 25


def test_semantic_component_is_bounded_and_validated():
    scored = score_candidate(
        set(),
        {
            "title": "",
            "skill_tags": [],
            "retrieval": {"semantic_score": 0.75},
        },
        today=TODAY,
    )
    assert scored.signals["components"]["semantic"] == 9

    ignored = score_candidate(
        set(),
        {
            "title": "",
            "skill_tags": [],
            "retrieval": {"semantic_score": 4.0},
        },
        today=TODAY,
    )
    assert ignored.signals["components"]["semantic"] == 12  # clamped, not raw

    boolean = score_candidate(
        set(),
        {"title": "", "skill_tags": [], "retrieval": {"semantic_score": True}},
        today=TODAY,
    )
    assert "semantic" not in boolean.signals["components"]


def test_unmatched_requirement_tags_surface_gaps():
    scored = score_candidate(
        {"python"},
        {
            "title": "",
            "skill_tags": ["python"],
            "requirement_tags": ["docker", "kubernetes"],
        },
        today=TODAY,
    )
    assert scored.signals["unmatched_requirement_tags"] == ["docker", "k8s", "kubernetes"]


def test_is_expired():
    assert is_expired({"application_deadline": "2026-08-28"}, today=TODAY) is True
    assert is_expired({"application_deadline": "2026-08-30"}, today=TODAY) is False
    assert is_expired({"application_deadline": None}, today=TODAY) is False
    assert is_expired({}, today=TODAY) is False


def test_company_diversity_caps_then_backfills_overflow():
    ranked = [{"company": "Acme", "job_id": str(index)} for index in range(6)] + [
        {"company": "Beta", "job_id": "beta"}
    ]
    selected = enforce_company_diversity(ranked, limit=5, max_per_company=3)
    # First pass caps Acme at 3 and takes Beta; the remaining slot is
    # backfilled from overflow (4th Acme) rather than returning 4 results.
    assert [item["company"] for item in selected] == [
        "Acme",
        "Acme",
        "Acme",
        "Beta",
        "Acme",
    ]


def test_company_diversity_keeps_cap_when_other_companies_fill_limit():
    ranked = [{"company": None, "job_id": str(index)} for index in range(4)] + [
        {"company": "Beta", "job_id": "b1"},
        {"company": "Beta", "job_id": "b2"},
    ]
    selected = enforce_company_diversity(ranked, limit=4, max_per_company=2)
    unknown = [item for item in selected if item["company"] is None]
    betas = [item for item in selected if item["company"] == "Beta"]
    assert len(unknown) == 2
    assert len(betas) == 2
    assert len(selected) == 4


def test_company_diversity_deduplicates_normalized_company_title_location():
    ranked = [
        {
            "company": "Acme, Inc.",
            "title": "Software Engineer Intern",
            "location": "Toronto, ON",
            "job_id": "one",
        },
        {
            "company": "ACME INC",
            "title": "Software Engineer Intern",
            "location": "Toronto, ON",
            "job_id": "two",
        },
    ]
    assert enforce_company_diversity(ranked, limit=5) == [ranked[0]]


def test_company_diversity_collapses_locations_and_pipe_aliases():
    ranked = [
        {
            "company": "Hewlett Packard Enterprise | HPE",
            "title": "Cloud Engineer Intern",
            "location": "Toronto",
            "job_id": "one",
        },
        {
            "company": "Hewlett Packard Enterprise",
            "title": "Cloud Engineer Intern",
            "location": "Vancouver",
            "job_id": "two",
        },
    ]
    assert enforce_company_diversity(ranked, limit=5) == [ranked[0]]


def _llm_config() -> LlmConfig:
    return LlmConfig(provider="OpenAI", model_name="gpt-test", api_key="key")


def test_rerank_applies_valid_adjustments():
    payload = SimpleNamespace(
        data={
            "adjustments": [
                {"candidate": 1, "delta": 3, "reason": "Closer skill overlap."},
                {"candidate": 0, "delta": -2, "reason": "Weaker role alignment."},
            ]
        }
    )
    with patch(
        "wecanfindintern.agent.recommend.rerank.complete_json", return_value=payload
    ):
        outcome = rerank_with_llm(
            llm_config=_llm_config(),
            candidates=[{"title": "A"}, {"title": "B"}],
            profile_summary={},
            preferences={},
        )
    assert outcome is not None
    assert outcome.adjustments == {1: 3, 0: -2}
    assert outcome.reasons[1] == "Closer skill overlap."
    assert outcome.status == "applied"


@pytest.mark.parametrize(
    "payload",
    [
        SimpleNamespace(data={"adjustments": [{"candidate": 9, "delta": 1, "reason": "x"}]}),
        SimpleNamespace(data={"adjustments": [{"candidate": 0, "delta": 50, "reason": "x"}]}),
        SimpleNamespace(data={"adjustments": [{"candidate": 0, "delta": 1, "reason": " "}]}),
        SimpleNamespace(data={"adjustments": [{"candidate": True, "delta": 1, "reason": "x"}]}),
        SimpleNamespace(
            data={
                "adjustments": [
                    {"candidate": 0, "delta": 1, "reason": "x"},
                    {"candidate": 0, "delta": 2, "reason": "dup"},
                ]
            }
        ),
        SimpleNamespace(data={"adjustments": "not-a-list"}),
        SimpleNamespace(data=None),
    ],
)
def test_rerank_rejects_invalid_payloads(payload):
    with patch(
        "wecanfindintern.agent.recommend.rerank.complete_json", return_value=payload
    ):
        outcome = rerank_with_llm(
            llm_config=_llm_config(),
            candidates=[{"title": "A"}, {"title": "B"}],
            profile_summary={},
            preferences={},
        )
    assert outcome.adjustments.keys() <= {0}
    assert outcome.status in {"invalid_response", "no_adjustment"}


def test_rerank_reports_transport_failure():
    with patch(
        "wecanfindintern.agent.recommend.rerank.complete_json",
        side_effect=LLMError("OpenAI", "boom"),
    ):
        outcome = rerank_with_llm(
            llm_config=_llm_config(),
            candidates=[{"title": "A"}, {"title": "B"}],
            profile_summary={},
            preferences={},
        )
    assert outcome.status == "failed"
    assert outcome.error_type == "LLMError"
    assert outcome.adjustments == {}


def test_rerank_skips_short_lists():
    outcome = rerank_with_llm(
        llm_config=_llm_config(),
        candidates=[{"title": "A"}],
        profile_summary={},
        preferences={},
    )
    assert outcome.status == "skipped"
    assert outcome.adjustments == {}
