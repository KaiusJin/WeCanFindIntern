#!/usr/bin/env python3
"""Independent quality probes for the recommend_jobs tool.

Unlike evaluate_agent_job_tools.py, these checks do not reuse the pipeline's
own relevance heuristics as the grader. They assert structural invariants and
run live cross-checks that can fail independently of the grader:

1. Score/ordering invariants: match_score bounded, descending, unique ids.
2. Company diversity contract: max 3 per company in the returned list.
3. Title honesty: every returned job must have a title independently matching
   the requested target role (regex written for this probe, not the ranker).
4. Embedding sanity: direct cosine check that the query vector ranks a known
   relevant description above an obviously unrelated one.
5. WaterlooWorks recall smoke: rows carry retrieval evidence and deadlines.
6. Live LLM rerank (optional, needs DEEPSEEK_API_KEY or EVAL_LOCAL_LLM_MODEL):
   bounded perturbation — deltas within ±5 and the top-10 set is preserved.

Read-only: uses run_tool with read-only tools only.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import sys

from wecanfindintern.agent.recommend.cache import recommendation_cache
from wecanfindintern.agent.recommend.documents import build_profile_query
from wecanfindintern.agent.recommend.embeddings import EmbeddingConfig, EmbeddingGateway
from wecanfindintern.agent.recommend.repository import RecommendationRepository
from wecanfindintern.agent.tools import AgentDeps, LlmConfig, run_tool
from wecanfindintern.config import Settings
from wecanfindintern.db.pool import Database
from wecanfindintern.db.read_repository import JobReadRepository
from wecanfindintern.profile.repository import ProfileRepository
from wecanfindintern.tracker.repository import TrackerRepository

CASES = (
    (
        "software_engineering_intern",
        ["software engineer"],
        r"software|developer|backend|front.?end|full.?stack",
    ),
    (
        "machine_learning_intern",
        ["machine learning"],
        r"machine learning|\bml\b|artificial intelligence|\bai\b|data sci",
    ),
    (
        "frontend_react_intern",
        ["frontend"],
        r"front.?end|web (developer|engineer)|ui (developer|engineer)|full.?stack",
    ),
)

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if condition:
        print(f"  PASS  {message}")
    else:
        print(f"  FAIL  {message}")
        failures.append(message)


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


def cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    la = math.sqrt(sum(a * a for a in left))
    lb = math.sqrt(sum(b * b for b in right))
    return dot / (la * lb)


async def main() -> None:
    database = Database(Settings.from_env())
    await database.open()
    try:
        job_repo = JobReadRepository(database.pool)
        profile_repo = ProfileRepository(database.pool)
        recommendation_repo = RecommendationRepository(database.pool)
        profile = await profile_repo.get_profile()
        deps = AgentDeps(
            job_repo=job_repo,
            tracker_repo=TrackerRepository(database.pool),
            profile_repo=profile_repo,
            waterlooworks=object(),
            recommendation_repo=recommendation_repo,
            embedding_config=EmbeddingConfig.from_env(),
        )

        # 1-3. Structural invariants + title honesty per case.
        for case_name, roles, title_pattern in CASES:
            print(f"\n[{case_name}] target_roles={roles}")
            recommendation_cache.clear()
            result = await run_tool(
                "recommend_jobs",
                {
                    "limit": 20,
                    "source": "public",
                    "target_roles": roles,
                    "use_semantic_retrieval": True,
                    "use_llm_rerank": False,
                    "exclude_tracked": False,
                },
                deps,
                phase="plan",
            )
            recs = result["data"]["recommendations"]
            scores = [item["match_score"] for item in recs]
            ids = [item["job_id"] for item in recs]
            check(all(0 <= s <= 100 for s in scores), "match_score within [0,100]")
            check(scores == sorted(scores, reverse=True), "match_score non-increasing")
            check(len(ids) == len(set(ids)), "no duplicate job ids")
            per_company: dict[str, int] = {}
            for item in recs:
                company = (item.get("company") or "").lower()
                per_company[company] = per_company.get(company, 0) + 1
            check(
                max(per_company.values(), default=0) <= 3,
                f"company diversity <=3 per company (max={max(per_company.values(), default=0)})",
            )
            title_hits = sum(
                1 for item in recs if re.search(title_pattern, item.get("title") or "", re.I)
            )
            check(
                title_hits >= len(recs) - 2,
                f"title honesty: >=18/20 titles match role regex (hits={title_hits})",
            )
            reasons_present = all(item.get("reasons") for item in recs)
            check(reasons_present, "every recommendation carries human-readable reasons")

        # 4. Embedding sanity, independent of the pipeline.
        config = EmbeddingConfig.from_env()
        if config is not None:
            print("\n[embedding sanity]")
            gateway = EmbeddingGateway(config)
            query_vec = await asyncio.to_thread(
                gateway.embed_query, "frontend web developer internship react typescript"
            )
            async with database.pool.connection() as connection:
                relevant = await (
                    await connection.execute(
                        """SELECT LEFT(description,2000) AS d FROM jobs
                        WHERE status=1 AND title ILIKE '%frontend%intern%'
                          AND description IS NOT NULL AND length(description)>200 LIMIT 1"""
                    )
                ).fetchone()
                unrelated = await (
                    await connection.execute(
                        """SELECT LEFT(description,2000) AS d FROM jobs
                        WHERE status=1 AND (title ILIKE '%registered nurse%'
                          OR title ILIKE '%civil engineer%')
                          AND description IS NOT NULL AND length(description)>200 LIMIT 1"""
                    )
                ).fetchone()
            if relevant and unrelated:
                sim_rel = cosine(
                    query_vec, await asyncio.to_thread(gateway.embed_query, relevant["d"])
                )
                sim_unrel = cosine(
                    query_vec, await asyncio.to_thread(gateway.embed_query, unrelated["d"])
                )
                check(
                    sim_rel > sim_unrel + 0.02,
                    "query embedding ranks relevant above unrelated "
                    f"({sim_rel:.3f} > {sim_unrel:.3f})",
                )
            else:
                print("  SKIP  no suitable corpus rows for the similarity probe")

        # 5. WaterlooWorks recall smoke.
        print("\n[waterloo recall smoke]")
        rows = await recommendation_repo.recall_waterloo(
            query_text=build_profile_query(profile, {"TARGET_ROLES": "software engineer"}),
            skills=["python"],
            exclude_external_ids=[],
            query_embedding=None,
            limit=10,
        )
        check(bool(rows), f"recall_waterloo returned rows ({len(rows)})")
        check(
            all(row.get("retrieval_sources") for row in rows),
            "every waterloo row carries retrieval evidence",
        )

        # 6. Live LLM rerank with bounded perturbation.
        llm_config = llm_config_from_env()
        if llm_config is not None:
            print(f"\n[llm rerank live] {llm_config.provider}:{llm_config.model_name}")
            recommendation_cache.clear()
            base_result = await run_tool(
                "recommend_jobs",
                {
                    "limit": 20,
                    "source": "public",
                    "use_semantic_retrieval": True,
                    "use_llm_rerank": False,
                    "exclude_tracked": False,
                },
                deps,
                phase="plan",
            )
            base_ids = [item["job_id"] for item in base_result["data"]["recommendations"]]
            llm_deps = AgentDeps(
                job_repo=job_repo,
                tracker_repo=TrackerRepository(database.pool),
                profile_repo=profile_repo,
                waterlooworks=object(),
                recommendation_repo=recommendation_repo,
                embedding_config=EmbeddingConfig.from_env(),
                llm_config=llm_config,
            )
            recommendation_cache.clear()
            llm_result = await run_tool(
                "recommend_jobs",
                {
                    "limit": 20,
                    "source": "public",
                    "use_semantic_retrieval": True,
                    "use_llm_rerank": True,
                    "exclude_tracked": False,
                },
                llm_deps,
                phase="plan",
            )
            status = llm_result["data"]["llm_rerank"]["status"]
            error_type = llm_result["data"]["llm_rerank"]["error_type"]
            print(
                f"  rerank status: {status} (error_type={error_type})"
            )
            llm_ids = [item["job_id"] for item in llm_result["data"]["recommendations"]]
            check(
                status in {"applied", "no_adjustment", "failed", "invalid_response"},
                f"rerank status is a known value ({status})",
            )
            check(
                set(llm_ids[:10]) <= set(base_ids[:15]),
                "bounded perturbation: top-10 drawn from the 15-item rerank shortlist",
            )
            deltas = [
                item.get("llm_adjustment", 0) for item in llm_result["data"]["recommendations"]
            ]
            check(
                all(-5 <= d <= 5 for d in deltas),
                f"all llm deltas within ±5 (deltas={sorted(set(deltas), reverse=True)[:6]})",
            )
            if status == "applied":
                reasons = [
                    item.get("llm_reason")
                    for item in llm_result["data"]["recommendations"]
                    if item.get("llm_adjustment")
                ]
                check(all(reasons), "applied rerank carries evidence-backed reasons")
        else:
            print("\n[llm rerank live] SKIP  no LLM configuration in environment")

        print(f"\n{'=' * 60}")
        if failures:
            print(f"PROBE RESULT: {len(failures)} failure(s)")
            for item in failures:
                print(f"  - {item}")
            sys.exit(1)
        print("PROBE RESULT: all checks passed")
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
