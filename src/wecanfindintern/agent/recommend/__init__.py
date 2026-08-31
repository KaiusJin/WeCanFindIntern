"""Recommendation pipeline: recall (repo), rule scoring, LLM re-rank, cache."""

from wecanfindintern.agent.recommend.cache import RecommendationCache, recommendation_cache
from wecanfindintern.agent.recommend.rerank import RerankOutcome, rerank_with_llm
from wecanfindintern.agent.recommend.scoring import (
    ScoredCandidate,
    enforce_company_diversity,
    expand_target_roles,
    is_expired,
    score_candidate,
    target_role_matches,
)

__all__ = [
    "RecommendationCache",
    "recommendation_cache",
    "RerankOutcome",
    "rerank_with_llm",
    "ScoredCandidate",
    "enforce_company_diversity",
    "expand_target_roles",
    "is_expired",
    "score_candidate",
    "target_role_matches",
]
