"""Read-only offline evaluation for the Agent job search/recommendation tools.

The labels are deterministic relevance heuristics over the current local job
corpus.  They are useful for regression testing, but are not a substitute for
human relevance judgements.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from wecanfindintern.agent.recommend.cache import recommendation_cache
from wecanfindintern.agent.recommend.documents import build_profile_query
from wecanfindintern.agent.recommend.embeddings import EmbeddingConfig, EmbeddingGateway
from wecanfindintern.agent.recommend.repository import (
    RecommendationFilters,
    RecommendationRepository,
)
from wecanfindintern.agent.recommend.scoring import expand_target_roles
from wecanfindintern.agent.tools import AgentDeps, LlmConfig, run_tool
from wecanfindintern.config import Settings
from wecanfindintern.db.pool import Database
from wecanfindintern.db.read_repository import JobReadRepository
from wecanfindintern.profile.repository import ProfileRepository
from wecanfindintern.tracker.repository import TrackerRepository


@dataclass(frozen=True)
class EvalCase:
    name: str
    search_args: dict[str, Any]
    target_roles: list[str]
    title_patterns: tuple[str, ...]
    categories: tuple[str, ...]
    skills: tuple[str, ...]


CASES = (
    EvalCase(
        "software_engineering_intern",
        {"query": "software engineer intern"},
        ["software engineer"],
        (r"software (engineer|developer)", r"backend", r"front.?end", r"full.?stack"),
        ("software_engineering",),
        ("python", "java", "javascript", "typescript", "cpp"),
    ),
    EvalCase(
        "machine_learning_intern",
        {"query": "machine learning intern"},
        ["machine learning"],
        (r"machine learning", r"\bml\b", r"artificial intelligence", r"\bai\b", r"data scientist"),
        ("data_ai", "research"),
        ("python", "pytorch", "tensorflow", "llm"),
    ),
    EvalCase(
        "frontend_react_intern",
        {"query": "frontend intern", "skill": "react"},
        ["frontend"],
        (r"front.?end", r"full.?stack", r"web (developer|engineer)", r"ui (developer|engineer)"),
        ("software_engineering",),
        ("react", "javascript", "typescript"),
    ),
    EvalCase(
        "devops_cloud_intern",
        {"query": "devops intern", "category": "cloud_devops"},
        ["devops"],
        (r"devops", r"site reliability", r"\bsre\b", r"cloud engineer", r"platform engineer"),
        ("cloud_devops",),
        ("aws", "azure", "gcp", "docker", "kubernetes", "terraform"),
    ),
    EvalCase(
        "data_analyst_intern",
        {"query": "data analyst intern"},
        ["data analyst"],
        (r"data analyst", r"business intelligence", r"\bbi analyst", r"analytics"),
        ("data_ai",),
        ("sql", "python", "power_bi", "tableau"),
    ),
)


def _has_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def grade(case: EvalCase, job: dict[str, Any]) -> int:
    title = job["title"] or ""
    title_match = _has_any(title, case.title_patterns)
    category_match = job.get("job_category") in case.categories
    tags = set(job.get("skill_tags") or [])
    skill_overlap = len(tags.intersection(case.skills))
    intern = (
        job.get("opportunity_type") == "internship"
        or _has_any(title, (r"\bintern(ship)?\b", r"\bco[ -]?op\b"))
    )
    return (2 if title_match else 1 if category_match else 0) + min(2, skill_overlap) + int(intern)


def generic_grade(profile_skills: set[str], job: dict[str, Any]) -> int:
    relevant_categories = {
        "software_engineering",
        "data_ai",
        "cloud_devops",
        "cybersecurity",
        "hardware_embedded",
    }
    overlap = len(profile_skills.intersection(job.get("skill_tags") or []))
    title = job.get("title") or ""
    intern = (
        job.get("opportunity_type") == "internship"
        or _has_any(title, (r"\bintern(ship)?\b", r"\bco[ -]?op\b"))
    )
    return (
        (2 if job.get("job_category") in relevant_categories else 0)
        + min(2, overlap)
        + int(intern)
    )


def metrics(
    ranked_ids: list[str],
    corpus: dict[str, dict[str, Any]],
    grader: Callable[[dict[str, Any]], int],
    *,
    relevant_threshold: int = 3,
) -> dict[str, Any]:
    grades = [grader(corpus[job_id]) for job_id in ranked_ids if job_id in corpus]
    relevant_total = sum(grader(job) >= relevant_threshold for job in corpus.values())

    def precision_at(k: int) -> float:
        top = grades[:k]
        return sum(value >= relevant_threshold for value in top) / k

    def dcg(values: list[int]) -> float:
        return sum((2**value - 1) / math.log2(index + 2) for index, value in enumerate(values))

    ideal = sorted((grader(job) for job in corpus.values()), reverse=True)[:10]
    first = next(
        (index + 1 for index, value in enumerate(grades) if value >= relevant_threshold),
        None,
    )
    retrieved_relevant = sum(value >= relevant_threshold for value in grades)
    top_jobs = [corpus[job_id] for job_id in ranked_ids[:10] if job_id in corpus]
    unique_pairs = {
        ((job.get("title") or "").lower(), (job.get("company_name") or "").lower())
        for job in top_jobs
    }
    unique_companies = {(job.get("company_name") or "").lower() for job in top_jobs}
    internship_count = sum(
        job.get("opportunity_type") == "internship"
        or _has_any(job.get("title") or "", (r"\bintern(ship)?\b", r"\bco[ -]?op\b"))
        for job in top_jobs
    )
    return {
        "returned": len(grades),
        "gold_relevant": relevant_total,
        "precision@5": round(precision_at(5), 3),
        "precision@10": round(precision_at(10), 3),
        "recall@returned": (
            round(retrieved_relevant / relevant_total, 3) if relevant_total else None
        ),
        "mrr": round(1 / first, 3) if first else 0.0,
        "ndcg@10": round(dcg(grades[:10]) / dcg(ideal), 3) if dcg(ideal) else None,
        "internship_rate@10": round(internship_count / 10, 3),
        "unique_title_company@10": len(unique_pairs),
        "unique_companies@10": len(unique_companies),
        "grades@10": grades[:10],
    }


async def load_corpus(database: Database) -> dict[str, dict[str, Any]]:
    async with database.pool.connection() as connection:
        rows = await (
            await connection.execute(
                """SELECT public_id::text AS job_id,title,company_name,job_category,
                          opportunity_type,skill_tags,published_sort_at
                   FROM jobs WHERE status=1"""
            )
        ).fetchall()
    return {row["job_id"]: dict(row) for row in rows}


def llm_config_from_env() -> LlmConfig | None:
    local_model = os.getenv("EVAL_LOCAL_LLM_MODEL", "").strip()
    if local_model:
        base = os.getenv("OLLAMA_API_BASE") or os.getenv("RECOMMEND_EMBEDDING_API_BASE")
        return LlmConfig(
            provider="Ollama",
            model_name=local_model,
            api_key="local",
            api_base=f"{base.rstrip('/')}/v1" if base else None,
        )
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        return None
    return LlmConfig(
        provider="DeepSeek",
        model_name=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        api_key=key,
        api_base=os.getenv("DEEPSEEK_API_BASE") or None,
    )


async def main() -> None:
    database = Database(Settings.from_env())
    await database.open()
    try:
        corpus = await load_corpus(database)
        job_repo = JobReadRepository(database.pool)
        profile_repo = ProfileRepository(database.pool)
        tracker_repo = TrackerRepository(database.pool)
        recommendation_repo = RecommendationRepository(database.pool)
        profile = await profile_repo.get_profile()
        profile_skills = {entry.name.strip().lower() for entry in profile.skills}
        embedding_config = EmbeddingConfig.from_env()
        embeddings_ready = bool(
            embedding_config and await recommendation_repo.has_embeddings(embedding_config)
        )
        embedder = (
            EmbeddingGateway(embedding_config)
            if embedding_config is not None and embeddings_ready
            else None
        )
        base_deps = AgentDeps(
            job_repo=job_repo,
            tracker_repo=tracker_repo,
            profile_repo=profile_repo,
            waterlooworks=object(),
            recommendation_repo=recommendation_repo,
            embedding_config=embedding_config,
        )

        report: dict[str, Any] = {
            "corpus_size": len(corpus),
            "profile_skill_count": len(profile_skills),
            "embedding_config": embedding_config.version if embedding_config else None,
            "embeddings_ready": embeddings_ready,
            "search": {},
            "recommend": {},
        }

        for case in CASES:
            search_result = await run_tool(
                "search_jobs",
                {**case.search_args, "source": "public", "limit": 30},
                base_deps,
                phase="plan",
            )
            search_ids = [item["job_id"] for item in search_result["data"]["public"]]
            report["search"][case.name] = {
                "args": case.search_args,
                **metrics(search_ids, corpus, lambda job, item=case: grade(item, job)),
                "top5": [
                    {
                        "title": corpus[job_id]["title"],
                        "company": corpus[job_id]["company_name"],
                        "grade": grade(case, corpus[job_id]),
                    }
                    for job_id in search_ids[:5]
                ],
            }

            query_text = build_profile_query(
                profile, {"TARGET_ROLES": ", ".join(case.target_roles)}
            )
            retrieval_filters = RecommendationFilters(
                target_roles=expand_target_roles(case.target_roles)
            )
            recalled = await recommendation_repo.recall_public(
                query_text=query_text,
                skills=sorted(profile_skills),
                exclude_public_ids=[],
                query_embedding=None,
                embedding_config=None,
                limit=240,
                filters=retrieval_filters,
            )
            recalled_ids = [str(row["item"].id) for row in recalled]
            query_embedding = (
                await asyncio.to_thread(embedder.embed_query, query_text) if embedder else None
            )
            hybrid_recalled = (
                await recommendation_repo.recall_public(
                    query_text=query_text,
                    skills=sorted(profile_skills),
                    exclude_public_ids=[],
                    query_embedding=query_embedding,
                    embedding_config=embedding_config,
                    limit=240,
                    filters=retrieval_filters,
                )
                if query_embedding is not None
                else recalled
            )
            hybrid_recalled_ids = [str(row["item"].id) for row in hybrid_recalled]
            lexical_tool_result = await run_tool(
                "recommend_jobs",
                {
                    "source": "public",
                    "limit": 20,
                    "target_roles": case.target_roles,
                    "use_semantic_retrieval": False,
                    "use_llm_rerank": False,
                    "exclude_tracked": False,
                },
                base_deps,
                phase="plan",
            )
            lexical_recommendation_ids = [
                item["job_id"]
                for item in lexical_tool_result["data"]["recommendations"]
            ]
            tool_result = await run_tool(
                "recommend_jobs",
                {
                    "source": "public",
                    "limit": 20,
                    "target_roles": case.target_roles,
                    "use_semantic_retrieval": True,
                    "use_llm_rerank": False,
                    "exclude_tracked": False,
                },
                base_deps,
                phase="plan",
            )
            recommendation_ids = [
                item["job_id"] for item in tool_result["data"]["recommendations"]
            ]
            recommendation_by_id = {
                item["job_id"]: item for item in tool_result["data"]["recommendations"]
            }
            report["recommend"][case.name] = {
                "target_roles": case.target_roles,
                "lexical_retrieval": metrics(
                    recalled_ids, corpus, lambda job, item=case: grade(item, job)
                ),
                "hybrid_retrieval": metrics(
                    hybrid_recalled_ids,
                    corpus,
                    lambda job, item=case: grade(item, job),
                ),
                "lexical_final": metrics(
                    lexical_recommendation_ids,
                    corpus,
                    lambda job, item=case: grade(item, job),
                ),
                "hybrid_final": metrics(
                    recommendation_ids, corpus, lambda job, item=case: grade(item, job)
                ),
                "tool_candidate_counts": tool_result["data"]["candidate_counts"],
                "retrieval_mode": tool_result["data"]["retrieval_mode"],
                "timings_ms": tool_result["data"]["timings_ms"],
                "top5": [
                    {
                        "title": corpus[job_id]["title"],
                        "company": corpus[job_id]["company_name"],
                        "grade": grade(case, corpus[job_id]),
                        "match_score": recommendation_by_id[job_id]["match_score"],
                    }
                    for job_id in recommendation_ids[:5]
                ],
            }

        recommendation_cache.clear()
        generic_lexical = await run_tool(
            "recommend_jobs",
            {
                "source": "public",
                "limit": 20,
                "use_semantic_retrieval": False,
                "use_llm_rerank": False,
                "exclude_tracked": False,
            },
            base_deps,
            phase="plan",
        )
        generic_lexical_ids = [
            item["job_id"] for item in generic_lexical["data"]["recommendations"]
        ]
        recommendation_cache.clear()
        generic_no_llm = await run_tool(
            "recommend_jobs",
            {
                "source": "public",
                "limit": 20,
                "use_semantic_retrieval": True,
                "use_llm_rerank": False,
                "exclude_tracked": False,
            },
            base_deps,
            phase="plan",
        )
        no_llm_ids = [item["job_id"] for item in generic_no_llm["data"]["recommendations"]]
        no_llm_by_id = {
            item["job_id"]: item for item in generic_no_llm["data"]["recommendations"]
        }
        generic: dict[str, Any] = {
            "lexical_only": {
                **metrics(
                    generic_lexical_ids,
                    corpus,
                    lambda job: generic_grade(profile_skills, job),
                ),
                "retrieval_mode": generic_lexical["data"]["retrieval_mode"],
                "candidate_counts": generic_lexical["data"]["candidate_counts"],
                "timings_ms": generic_lexical["data"]["timings_ms"],
            },
            "without_llm": {
                **metrics(no_llm_ids, corpus, lambda job: generic_grade(profile_skills, job)),
                "retrieval_mode": generic_no_llm["data"]["retrieval_mode"],
                "candidate_counts": generic_no_llm["data"]["candidate_counts"],
                "timings_ms": generic_no_llm["data"]["timings_ms"],
                "top10": [
                    {
                        "job_id": job_id,
                        "title": corpus[job_id]["title"],
                        "company": corpus[job_id]["company_name"],
                        "grade": generic_grade(profile_skills, corpus[job_id]),
                        "match_score": no_llm_by_id[job_id]["match_score"],
                        "confidence": no_llm_by_id[job_id]["confidence"],
                    }
                    for job_id in no_llm_ids[:10]
                ],
            }
        }
        llm_config = (
            llm_config_from_env()
            if os.getenv("EVAL_ENABLE_EXTERNAL_LLM") == "1"
            or os.getenv("EVAL_LOCAL_LLM_MODEL")
            else None
        )
        if llm_config is not None:
            recommendation_cache.clear()
            llm_deps = AgentDeps(
                job_repo=job_repo,
                tracker_repo=tracker_repo,
                profile_repo=profile_repo,
                waterlooworks=object(),
                recommendation_repo=recommendation_repo,
                embedding_config=embedding_config,
                llm_config=llm_config,
            )
            generic_llm = await run_tool(
                "recommend_jobs",
                {
                    "source": "public",
                    "limit": 20,
                    "use_semantic_retrieval": True,
                    "use_llm_rerank": True,
                    "exclude_tracked": False,
                },
                llm_deps,
                phase="plan",
            )
            llm_ids = [item["job_id"] for item in generic_llm["data"]["recommendations"]]
            generic["with_llm"] = {
                **metrics(llm_ids, corpus, lambda job: generic_grade(profile_skills, job)),
                "changed_positions_in_top10": sum(
                    left != right
                    for left, right in zip(no_llm_ids[:10], llm_ids[:10], strict=False)
                ),
                "same_top10_set": set(no_llm_ids[:10]) == set(llm_ids[:10]),
                "timings_ms": generic_llm["data"]["timings_ms"],
                "llm_rerank": generic_llm["data"]["llm_rerank"],
                "top10": [
                    {
                        "job_id": job_id,
                        "title": corpus[job_id]["title"],
                        "company": corpus[job_id]["company_name"],
                        "grade": generic_grade(profile_skills, corpus[job_id]),
                    }
                    for job_id in llm_ids[:10]
                ],
            }
        report["recommend"]["generic_profile"] = generic
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
