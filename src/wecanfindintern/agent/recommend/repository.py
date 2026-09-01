"""Fixed-query hybrid retrieval over recommendation documents and pgvector."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from wecanfindintern.agent.recommend.documents import vector_literal
from wecanfindintern.agent.recommend.embeddings import EmbeddingConfig
from wecanfindintern.application.job_models import JobListItem
from wecanfindintern.db.job_projection import JOB_SELECT, job_list_item
from wecanfindintern.domain.classification import normalize_tag

RRF_K = 60
LEXICAL_LIMIT = 200
SEMANTIC_CHUNK_LIMIT = 300
SEMANTIC_RRF_WEIGHT = 0.7
LEXICAL_FLOOR_RATIO = 0.5


@dataclass(frozen=True, slots=True)
class RecommendationFilters:
    """Eligibility constraints applied before retrieval ranking and truncation."""

    target_roles: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()
    work_modes: tuple[str, ...] = ()
    opportunity_types: tuple[str, ...] = ()


class RecommendationRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def available(self) -> bool:
        async with self.pool.connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT to_regclass('recommendation_documents') IS NOT NULL AS ready;"
                )
            ).fetchone()
        return bool(row and row["ready"])

    async def corpus_version(self) -> str:
        if not await self.available():
            return "unindexed"
        async with self.pool.connection() as connection:
            row = await (
                await connection.execute(
                    """SELECT corpus_version,updated_at
                    FROM recommendation_corpus_state WHERE state_key='default';"""
                )
            ).fetchone()
        return f"{row['corpus_version']}:{row['updated_at'].isoformat()}" if row else "0"

    async def has_embeddings(
        self, config: EmbeddingConfig, *, source: str | None = None
    ) -> bool:
        if not await self.available():
            return False
        async with self.pool.connection() as connection:
            row = await (
                await connection.execute(
                    """SELECT
                        count(e.chunk_id) >= greatest(1,ceil(count(c.id)*0.95)) AS present
                    FROM recommendation_chunks c
                    JOIN recommendation_documents d ON d.id=c.document_id
                    LEFT JOIN recommendation_chunk_embeddings e
                      ON e.chunk_id=c.id AND e.provider=%s AND e.model=%s
                     AND e.dimensions=%s
                    WHERE c.chunk_index=0
                      AND (%s::text IS NULL OR d.source=%s::text);""",
                    (
                        config.provider,
                        config.model,
                        config.dimensions,
                        source,
                        source,
                    ),
                )
            ).fetchone()
        return bool(row and row["present"])

    async def recall_public(
        self,
        *,
        query_text: str,
        skills: list[str],
        exclude_public_ids: list[UUID],
        query_embedding: list[float] | None,
        limit: int,
        embedding_config: EmbeddingConfig | None = None,
        filters: RecommendationFilters | None = None,
    ) -> list[dict[str, Any]]:
        if not await self.available():
            return []
        lexical = await self._lexical_source(
            source="public", query_text=query_text, skills=skills, filters=filters
        )
        semantic = (
            await self._semantic_source(
                "public", query_embedding, embedding_config, filters=filters
            )
            if query_embedding is not None and embedding_config is not None
            else []
        )
        fused: dict[str, float] = defaultdict(float)
        evidence: dict[str, dict[str, Any]] = defaultdict(dict)
        for rank, row in enumerate(lexical, start=1):
            job_id = row["source_job_id"]
            fused[job_id] += 1.0 / (RRF_K + rank)
            evidence[job_id]["lexical_rank"] = rank
            evidence[job_id]["lexical_score"] = float(row["score"] or 0)
        for rank, row in enumerate(semantic, start=1):
            job_id = row["source_job_id"]
            fused[job_id] += SEMANTIC_RRF_WEIGHT / (RRF_K + rank)
            evidence[job_id]["semantic_rank"] = rank
            evidence[job_id]["semantic_score"] = float(row["semantic_score"] or 0)
        ranked_ids = _ranked_ids_with_lexical_floor(
            lexical=lexical,
            fused=fused,
            excluded={str(value) for value in exclude_public_ids},
            limit=limit,
        )
        if not ranked_ids:
            return []
        details = await self._load_public_details(ranked_ids)
        by_id = {str(row["public_id"]): row for row in details}
        results: list[dict[str, Any]] = []
        for job_id in ranked_ids:
            row = by_id.get(job_id)
            if row is None:
                continue
            item: JobListItem = job_list_item(row)
            retrieval = evidence[job_id]
            retrieval["rrf_score"] = fused[job_id]
            sources = []
            if "lexical_rank" in retrieval:
                sources.append("full_text")
            if "semantic_rank" in retrieval:
                sources.append("vector")
            results.append(
                {
                    "item": item,
                    "description": row["description_excerpt"],
                    "url": row["application_url"],
                    "requirement_tags": row["requirement_tags"] or [],
                    "retrieval": retrieval,
                    "retrieval_sources": sources,
                }
            )
        return results

    async def recall_waterloo(
        self,
        *,
        query_text: str,
        skills: list[str],
        exclude_external_ids: list[str],
        query_embedding: list[float] | None,
        limit: int,
        embedding_config: EmbeddingConfig | None = None,
        filters: RecommendationFilters | None = None,
    ) -> list[dict[str, Any]]:
        if not await self.available():
            return []
        lexical = await self._lexical_source(
            source="waterloo_work",
            query_text=query_text,
            skills=skills,
            filters=filters,
        )
        semantic = (
            await self._semantic_source(
                "waterloo_work", query_embedding, embedding_config, filters=filters
            )
            if query_embedding is not None and embedding_config is not None
            else []
        )
        fused: dict[str, float] = defaultdict(float)
        evidence: dict[str, dict[str, Any]] = defaultdict(dict)
        for rank, row in enumerate(lexical, start=1):
            job_id = row["source_job_id"]
            fused[job_id] += 1.0 / (RRF_K + rank)
            evidence[job_id].update(
                lexical_rank=rank, lexical_score=float(row["score"] or 0)
            )
        for rank, row in enumerate(semantic, start=1):
            job_id = row["source_job_id"]
            fused[job_id] += SEMANTIC_RRF_WEIGHT / (RRF_K + rank)
            evidence[job_id].update(
                semantic_rank=rank,
                semantic_score=float(row["semantic_score"] or 0),
            )
        excluded = set(exclude_external_ids)
        ranked_ids = _ranked_ids_with_lexical_floor(
            lexical=lexical,
            fused=fused,
            excluded=excluded,
            limit=limit,
        )
        if not ranked_ids:
            return []
        async with self.pool.connection() as connection:
            rows = await (
                await connection.execute(
                    """SELECT source_job_id,title,role_family,normalized_skills,
                              requirement_tags,document_text,metadata
                    FROM recommendation_documents
                    WHERE source='waterloo_work' AND source_job_id=ANY(%s);""",
                    (ranked_ids,),
                )
            ).fetchall()
        by_id = {row["source_job_id"]: row for row in rows}
        results: list[dict[str, Any]] = []
        for job_id in ranked_ids:
            row = by_id.get(job_id)
            if row is None:
                continue
            metadata = row["metadata"] or {}
            retrieval = evidence[job_id]
            retrieval["rrf_score"] = fused[job_id]
            sources = []
            if "lexical_rank" in retrieval:
                sources.append("full_text")
            if "semantic_rank" in retrieval:
                sources.append("vector")
            results.append(
                {
                    "source_job_id": job_id,
                    "title": row["title"],
                    "organization": metadata.get("company"),
                    "division": metadata.get("division") or row["role_family"],
                    "location_text": metadata.get("location"),
                    "work_mode": metadata.get("work_mode"),
                    "opportunity_type": metadata.get("opportunity_type"),
                    "date_posted": metadata.get("date_posted"),
                    "application_deadline": metadata.get("application_deadline"),
                    "application_deadline_date": metadata.get(
                        "application_deadline_date"
                    ),
                    "application_url": metadata.get("application_url"),
                    "boards": metadata.get("boards") or [],
                    "description": row["document_text"][:3000],
                    "skill_tags": row["normalized_skills"] or [],
                    "requirement_tags": row["requirement_tags"] or [],
                    "retrieval": retrieval,
                    "retrieval_sources": sources,
                }
            )
        return results

    async def _lexical_source(
        self,
        *,
        source: str,
        query_text: str,
        skills: list[str],
        filters: RecommendationFilters | None,
    ) -> list[dict[str, Any]]:
        normalized_skills = sorted({normalize_tag(skill) for skill in skills if skill})
        # Full profile responsibilities belong in the embedding query. Feeding
        # thousands of prose tokens to websearch_to_tsquery makes lexical recall
        # both slow and overly restrictive, so lexical retrieval uses bounded
        # skill/role phrases only.
        phrases: list[str] = []
        target_line = next(
            (line for line in query_text.splitlines() if line.startswith("Target roles:")),
            "",
        )
        raw_phrases = [*skills[:40], *target_line.removeprefix("Target roles:").split(",")]
        for value in raw_phrases:
            cleaned = re.sub(r"[^\w#+. -]+", " ", value.lower()).strip()
            if cleaned and cleaned not in phrases:
                phrases.append(cleaned)
        search_text = " OR ".join(
            f'"{phrase}"' if " " in phrase else phrase for phrase in phrases
        )
        if not search_text and not normalized_skills:
            return []
        filter_sql, filter_parameters = _document_filter_sql("d", filters)
        sql = f"""
            WITH query AS (SELECT websearch_to_tsquery('simple', %s) AS tsq)
            SELECT d.source_job_id,
                   ts_rank_cd(d.search_document,query.tsq)
                   + CASE WHEN d.normalized_skills && %s::text[] THEN 1.0 ELSE 0 END AS score
            FROM recommendation_documents d,query
            WHERE d.source=%s AND (
                d.search_document @@ query.tsq
                OR d.normalized_skills && %s::text[]
            )
            AND (
                d.source <> 'public'
                OR EXISTS (
                    SELECT 1 FROM jobs active_job
                    WHERE active_job.public_id=d.public_job_id AND active_job.status=1
                )
            )
            {filter_sql}
            ORDER BY score DESC, d.indexed_at DESC
            LIMIT %s;
        """
        parameters = (
            search_text,
            normalized_skills,
            source,
            normalized_skills,
            *filter_parameters,
            LEXICAL_LIMIT,
        )
        async with self.pool.connection() as connection:
            return await (await connection.execute(sql, parameters)).fetchall()

    async def _semantic_source(
        self,
        source: str,
        query_embedding: list[float],
        config: EmbeddingConfig,
        *,
        filters: RecommendationFilters | None,
    ) -> list[dict[str, Any]]:
        vector = vector_literal(query_embedding)
        dimension = config.dimensions
        filter_sql, filter_parameters = _document_filter_sql(
            "source_document", filters
        )
        sql = f"""
            WITH nearest_chunks AS (
                SELECT c.document_id,
                       1-(e.embedding::vector({dimension}) <=> %s::vector({dimension}))
                           AS semantic_score
                FROM recommendation_chunk_embeddings e
                JOIN recommendation_chunks c ON c.id=e.chunk_id
                JOIN recommendation_documents source_document
                  ON source_document.id=c.document_id
                WHERE e.provider=%s AND e.model=%s AND e.dimensions=%s
                  AND c.chunk_index=0
                  AND source_document.source=%s
                  AND (
                      source_document.source <> 'public'
                      OR EXISTS (
                          SELECT 1 FROM jobs active_job
                          WHERE active_job.public_id=source_document.public_job_id
                            AND active_job.status=1
                      )
                  )
                  {filter_sql}
                ORDER BY e.embedding::vector({dimension}) <=> %s::vector({dimension})
                LIMIT %s
            )
            SELECT d.source_job_id,max(n.semantic_score) AS semantic_score
            FROM nearest_chunks n
            JOIN recommendation_documents d ON d.id=n.document_id
            WHERE d.source=%s
            GROUP BY d.source_job_id
            ORDER BY semantic_score DESC
            LIMIT %s;
        """
        async with self.pool.connection() as connection:
            return await (
                await connection.execute(
                    sql,
                    (
                        vector,
                        config.provider,
                        config.model,
                        dimension,
                        source,
                        *filter_parameters,
                        vector,
                        SEMANTIC_CHUNK_LIMIT,
                        source,
                        LEXICAL_LIMIT,
                    ),
                )
            ).fetchall()

    async def _load_public_details(self, ranked_ids: list[str]) -> list[dict[str, Any]]:
        sql = f"""
            SELECT {JOB_SELECT},LEFT(j.description,3000) AS description_excerpt,
                   j.requirement_tags,
                   (
                       SELECT coalesce(js.direct_url,js.source_url)
                       FROM job_sources js WHERE js.job_id=j.id
                       ORDER BY js.first_seen_at,js.id LIMIT 1
                   ) AS application_url
            FROM jobs j
            WHERE j.status=1 AND j.public_id=ANY(%s::uuid[]);
        """
        async with self.pool.connection() as connection:
            return await (await connection.execute(sql, (ranked_ids,))).fetchall()


def _document_filter_sql(
    alias: str, filters: RecommendationFilters | None
) -> tuple[str, list[Any]]:
    """Build fixed-column SQL predicates; all user values remain parameters."""

    if filters is None:
        return "", []
    predicates: list[str] = []
    parameters: list[Any] = []
    if filters.target_roles:
        predicates.append(f"lower({alias}.title) LIKE ANY(%s)")
        parameters.append([f"%{value.lower()}%" for value in filters.target_roles])
    if filters.locations:
        predicates.append(
            f"lower(coalesce({alias}.metadata->>'location','')) LIKE ANY(%s)"
        )
        parameters.append([f"%{value.lower()}%" for value in filters.locations])
    if filters.work_modes:
        predicates.append(
            f"lower(coalesce({alias}.metadata->>'work_mode','')) = ANY(%s)"
        )
        parameters.append([value.lower() for value in filters.work_modes])
    if filters.opportunity_types:
        predicates.append(
            f"lower(coalesce({alias}.metadata->>'opportunity_type','')) = ANY(%s)"
        )
        parameters.append([value.lower() for value in filters.opportunity_types])
    if not predicates:
        return "", []
    return "AND " + " AND ".join(predicates), parameters


def _ranked_ids_with_lexical_floor(
    *,
    lexical: list[dict[str, Any]],
    fused: dict[str, float],
    excluded: set[str],
    limit: int,
) -> list[str]:
    """Keep strong exact recall while semantic retrieval broadens the candidate set."""

    lexical_available = sum(
        row["source_job_id"] not in excluded for row in lexical
    )
    floor_count = min(
        lexical_available, max(1, round(limit * LEXICAL_FLOOR_RATIO))
    )
    ranked: list[str] = []
    seen = set(excluded)
    for row in lexical:
        job_id = row["source_job_id"]
        if job_id not in seen:
            ranked.append(job_id)
            seen.add(job_id)
        if len(ranked) >= floor_count:
            break
    for job_id, _ in sorted(fused.items(), key=lambda item: item[1], reverse=True):
        if job_id in seen:
            continue
        ranked.append(job_id)
        seen.add(job_id)
        if len(ranked) >= limit:
            break
    return ranked[:limit]
