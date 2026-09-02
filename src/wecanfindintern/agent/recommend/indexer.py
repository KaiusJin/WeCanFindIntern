"""Incremental builder for recommendation retrieval documents and embeddings."""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from wecanfindintern.agent.recommend.documents import (
    DOCUMENT_VERSION,
    build_public_document,
    build_waterloo_document,
    vector_literal,
)
from wecanfindintern.agent.recommend.embeddings import EmbeddingGateway
from wecanfindintern.waterlooworks.config import waterlooworks_database_path
from wecanfindintern.waterlooworks.records import decode_waterlooworks_job


def load_waterloo_jobs_from_sqlite(
    limit: int | None = None, *, path: Path | None = None
) -> list[dict[str, Any]]:
    """Read WaterlooWorks jobs from the local collection sqlite (read-only)."""

    resolved = (path or waterlooworks_database_path()).expanduser()
    if not resolved.exists():
        return []
    query = """
        SELECT j.*,
               (SELECT json_group_array(board) FROM (
                    SELECT board FROM waterlooworks_job_boards b
                    WHERE b.source_job_id=j.source_job_id
                    ORDER BY board
                )) AS boards
        FROM waterlooworks_jobs j
        ORDER BY j.last_seen_at DESC,j.source_job_id DESC
    """
    parameters: tuple[int, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        parameters = (limit,)
    with sqlite3.connect(f"file:{resolved}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(query, parameters).fetchall()
    return [decode_waterlooworks_job(row) for row in rows]


@dataclass(slots=True)
class IndexReport:
    scanned: int = 0
    updated: int = 0
    skipped: int = 0
    chunks_written: int = 0
    chunks_embedded: int = 0
    embedding_errors: int = 0
    indexing_errors: int = 0


class RecommendationIndexer:
    def __init__(
        self,
        pool: AsyncConnectionPool,
        *,
        embedder: EmbeddingGateway | None = None,
        page_size: int = 100,
        embedding_batch_size: int = 64,
    ) -> None:
        self.pool = pool
        self.embedder = embedder
        self.page_size = page_size
        self.embedding_batch_size = embedding_batch_size

    async def enqueue_stale_public_documents(self) -> int:
        """Queue active jobs whose derived document is absent or on an old version."""

        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO recommendation_index_queue(
                    public_job_id, queued_at, attempts, last_error
                )
                SELECT j.public_id, now(), 0, NULL
                FROM jobs j
                LEFT JOIN recommendation_documents d
                  ON d.source='public' AND d.public_job_id=j.public_id
                WHERE j.status=1
                  AND (d.id IS NULL OR d.index_version<>%s)
                ON CONFLICT(public_job_id) DO UPDATE SET
                    queued_at=EXCLUDED.queued_at,
                    attempts=0,
                    last_error=NULL
                RETURNING public_job_id;
                """,
                (DOCUMENT_VERSION,),
            )
            return len(await cursor.fetchall())

    async def index_public_jobs(self, *, limit: int | None = None) -> IndexReport:
        report = IndexReport()
        after_id = 0
        while limit is None or report.scanned < limit:
            page_limit = min(self.page_size, limit - report.scanned) if limit else self.page_size
            rows = await self._load_public_page(after_id=after_id, limit=page_limit)
            if not rows:
                break
            for row in rows:
                after_id = row["internal_id"]
                report.scanned += 1
                changed, chunks = await self._upsert_public_document(row)
                if not changed:
                    report.skipped += 1
                else:
                    report.updated += 1
                    report.chunks_written += len(chunks)
                    if self.embedder is not None and chunks:
                        try:
                            embedded = await self._embed_chunks(chunks[:1])
                            report.chunks_embedded += embedded
                        except Exception:
                            report.embedding_errors += 1
                async with self.pool.connection() as connection:
                    await connection.execute(
                        "DELETE FROM recommendation_index_queue WHERE public_job_id=%s;",
                        (row["public_id"],),
                    )
            if len(rows) < page_limit:
                break
        if report.updated:
            async with self.pool.connection() as connection:
                await connection.execute(
                    """UPDATE recommendation_corpus_state
                    SET corpus_version=corpus_version+1,updated_at=now()
                    WHERE state_key='default';"""
                )
        return report

    async def index_pending(
        self, *, limit: int = 100, max_attempts: int = 5
    ) -> IndexReport:
        """Process retryable queue entries without letting poison rows block the drain.

        Rows reaching ``max_attempts`` remain in the queue with their last error for
        inspection, but are excluded until a later job update resets their attempts.
        """

        report = IndexReport()
        sql = """
            SELECT j.id AS internal_id,j.public_id,j.title,j.company_name,
                   j.company_industry,j.location_text,j.work_mode,j.opportunity_type,
                   j.job_category,j.job_function,j.skill_tags,j.requirement_tags,
                   j.date_posted,j.description
            FROM recommendation_index_queue q
            JOIN jobs j ON j.public_id=q.public_job_id
            WHERE j.status=1
              AND q.attempts < %s
            ORDER BY q.queued_at,q.public_job_id
            LIMIT %s;
        """
        async with self.pool.connection() as connection:
            rows = await (
                await connection.execute(sql, (max_attempts, limit))
            ).fetchall()
        for row in rows:
            report.scanned += 1
            try:
                changed, chunks = await self._upsert_public_document(row)
                report.updated += int(changed)
                report.skipped += int(not changed)
                report.chunks_written += len(chunks)
            except Exception as error:
                await self._record_queue_failure(row["public_id"], error)
                report.indexing_errors += 1
                continue

            pending_embeddings = chunks[:1]
            if self.embedder is not None and not pending_embeddings:
                pending_embeddings = await self._missing_primary_embedding(
                    source="public", source_job_id=str(row["public_id"]), limit=1
                )
            if self.embedder is not None and pending_embeddings:
                try:
                    report.chunks_embedded += await self._embed_chunks(pending_embeddings)
                except Exception as error:
                    await self._record_queue_failure(row["public_id"], error)
                    report.embedding_errors += 1
                    continue
            async with self.pool.connection() as connection:
                await connection.execute(
                    "DELETE FROM recommendation_index_queue WHERE public_job_id=%s;",
                    (row["public_id"],),
                )
        if report.updated:
            async with self.pool.connection() as connection:
                await connection.execute(
                    """UPDATE recommendation_corpus_state
                    SET corpus_version=corpus_version+1,updated_at=now()
                    WHERE state_key='default';"""
                )
        return report

    async def index_waterloo_jobs(self, items: list[dict[str, Any]]) -> IndexReport:
        report = IndexReport()
        for item in items:
            if not item.get("source_job_id"):
                continue
            report.scanned += 1
            document = build_waterloo_document(item)
            changed, chunks = await self._upsert_external_document(document)
            report.updated += int(changed)
            report.skipped += int(not changed)
            report.chunks_written += len(chunks)
            pending_embeddings = chunks[:1]
            if self.embedder is not None and not pending_embeddings:
                pending_embeddings = await self._missing_primary_embedding(
                    source="waterloo_work",
                    source_job_id=str(item["source_job_id"]),
                    limit=1,
                )
            if self.embedder is not None and pending_embeddings:
                try:
                    report.chunks_embedded += await self._embed_chunks(pending_embeddings)
                except Exception:
                    report.embedding_errors += 1
        if report.updated:
            async with self.pool.connection() as connection:
                await connection.execute(
                    """UPDATE recommendation_corpus_state
                    SET corpus_version=corpus_version+1,updated_at=now()
                    WHERE state_key='default';"""
                )
        return report

    async def _record_queue_failure(self, public_job_id: Any, error: Exception) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """UPDATE recommendation_index_queue
                SET attempts=attempts+1,last_error=%s,queued_at=now()
                WHERE public_job_id=%s;""",
                (str(error)[:1000], public_job_id),
            )

    async def _missing_primary_embedding(
        self, *, source: str, source_job_id: str, limit: int
    ) -> list[tuple[int, str]]:
        if self.embedder is None:
            return []
        async with self.pool.connection() as connection:
            rows = await (
                await connection.execute(
                    """SELECT c.id,c.chunk_text
                    FROM recommendation_documents d
                    JOIN recommendation_chunks c ON c.document_id=d.id
                    WHERE d.source=%s AND d.source_job_id=%s AND c.chunk_index=0
                      AND NOT EXISTS (
                        SELECT 1 FROM recommendation_chunk_embeddings e
                        WHERE e.chunk_id=c.id AND e.provider=%s AND e.model=%s
                          AND e.dimensions=%s
                      )
                    ORDER BY c.id LIMIT %s;""",
                    (
                        source,
                        source_job_id,
                        self.embedder.config.provider,
                        self.embedder.config.model,
                        self.embedder.config.dimensions,
                        limit,
                    ),
                )
            ).fetchall()
        return [(row["id"], row["chunk_text"]) for row in rows]

    async def embed_missing_primary_chunks(
        self, *, limit: int | None = None
    ) -> IndexReport:
        """Embed the designated primary chunk for documents missing that vector."""
        report = IndexReport()
        if self.embedder is None:
            return report
        remaining = limit
        while remaining is None or remaining > 0:
            batch_limit = self.embedding_batch_size
            if remaining is not None:
                batch_limit = min(batch_limit, remaining)
            async with self.pool.connection() as connection:
                rows = await (
                    await connection.execute(
                        """SELECT c.id,c.chunk_text FROM recommendation_chunks c
                        WHERE c.chunk_index=0 AND NOT EXISTS (
                            SELECT 1 FROM recommendation_chunk_embeddings e
                            WHERE e.chunk_id=c.id AND e.provider=%s AND e.model=%s
                              AND e.dimensions=%s
                        ) ORDER BY c.id LIMIT %s;""",
                        (
                            self.embedder.config.provider,
                            self.embedder.config.model,
                            self.embedder.config.dimensions,
                            batch_limit,
                        ),
                    )
                ).fetchall()
            if not rows:
                break
            chunks = [(row["id"], row["chunk_text"]) for row in rows]
            report.scanned += len(chunks)
            try:
                report.chunks_embedded += await self._embed_chunks(chunks)
            except Exception:
                report.embedding_errors += len(chunks)
                break
            if remaining is not None:
                remaining -= len(chunks)
        if report.chunks_embedded:
            await self.ensure_vector_index()
            async with self.pool.connection() as connection:
                await connection.execute(
                    """UPDATE recommendation_corpus_state
                    SET corpus_version=corpus_version+1,updated_at=now()
                    WHERE state_key='default';"""
                )
        return report

    async def ensure_vector_index(self) -> None:
        """Create one HNSW expression index for the active embedding profile."""
        if self.embedder is None or self.embedder.config.dimensions > 2000:
            return
        config = self.embedder.config
        digest = hashlib.sha256(config.version.encode()).hexdigest()[:16]
        statement = sql.SQL(
            """CREATE INDEX IF NOT EXISTS {} ON recommendation_chunk_embeddings
            USING hnsw ((embedding::vector({})) vector_cosine_ops)
            WHERE provider={} AND model={} AND dimensions={};"""
        ).format(
            sql.Identifier(f"idx_recommendation_embedding_{digest}_hnsw"),
            sql.Literal(config.dimensions),
            sql.Literal(config.provider),
            sql.Literal(config.model),
            sql.Literal(config.dimensions),
        )
        async with self.pool.connection() as connection:
            await connection.execute(statement)

    async def _load_public_page(self, *, after_id: int, limit: int) -> list[dict[str, Any]]:
        sql = """
            SELECT id AS internal_id, public_id, title, company_name, company_industry,
                   location_text, work_mode, opportunity_type, job_category,
                   job_function, skill_tags, requirement_tags, date_posted, description
            FROM jobs
            WHERE status=1 AND id>%s
            ORDER BY id
            LIMIT %s;
        """
        async with self.pool.connection() as connection:
            return await (await connection.execute(sql, (after_id, limit))).fetchall()

    async def _upsert_public_document(
        self, row: dict[str, Any]
    ) -> tuple[bool, list[tuple[int, str]]]:
        document = build_public_document(row)
        async with self.pool.connection() as connection, connection.transaction():
            existing = await (
                await connection.execute(
                    """SELECT id,content_hash FROM recommendation_documents
                    WHERE source='public' AND source_job_id=%s FOR UPDATE;""",
                    (document.source_job_id,),
                )
            ).fetchone()
            if existing and existing["content_hash"] == document.content_hash:
                return False, []
            row_result = await (
                await connection.execute(
                    """INSERT INTO recommendation_documents (
                        source,source_job_id,public_job_id,content_hash,title,role_family,
                        normalized_skills,requirement_tags,document_text,
                        metadata,index_version,indexed_at
                    ) VALUES ('public',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                    ON CONFLICT (source,source_job_id) DO UPDATE SET
                        public_job_id=EXCLUDED.public_job_id,
                        content_hash=EXCLUDED.content_hash,
                        title=EXCLUDED.title,
                        role_family=EXCLUDED.role_family,
                        normalized_skills=EXCLUDED.normalized_skills,
                        requirement_tags=EXCLUDED.requirement_tags,
                        document_text=EXCLUDED.document_text,
                        metadata=EXCLUDED.metadata,
                        index_version=EXCLUDED.index_version,
                        indexed_at=now()
                    RETURNING id;""",
                    (
                        document.source_job_id,
                        document.public_job_id,
                        document.content_hash,
                        document.title,
                        document.role_family,
                        document.normalized_skills,
                        document.requirement_tags,
                        document.document_text,
                        Jsonb(document.metadata),
                        DOCUMENT_VERSION,
                    ),
                )
            ).fetchone()
            document_id = row_result["id"]
            chunk_rows = await self._sync_chunks(
                connection, document_id, document.chunks
            )
        return True, chunk_rows

    async def _sync_chunks(
        self, connection: Any, document_id: int, chunks: list[str]
    ) -> list[tuple[int, str]]:
        """Preserve embeddings for unchanged chunks during metadata/version refreshes."""

        existing_rows = await (
            await connection.execute(
                """SELECT id,chunk_index,chunk_hash
                FROM recommendation_chunks WHERE document_id=%s;""",
                (document_id,),
            )
        ).fetchall()
        existing = {row["chunk_index"]: row for row in existing_rows}
        changed: list[tuple[int, str]] = []
        for index, text in enumerate(chunks):
            chunk_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            current = existing.get(index)
            if current is not None and current["chunk_hash"] == chunk_hash:
                continue
            if current is not None:
                await connection.execute(
                    "DELETE FROM recommendation_chunks WHERE id=%s;",
                    (current["id"],),
                )
            inserted = await (
                await connection.execute(
                    """INSERT INTO recommendation_chunks (
                        document_id,chunk_index,chunk_type,chunk_text,chunk_hash
                    ) VALUES (%s,%s,'description',%s,%s) RETURNING id;""",
                    (document_id, index, text, chunk_hash),
                )
            ).fetchone()
            changed.append((inserted["id"], text))
        await connection.execute(
            "DELETE FROM recommendation_chunks WHERE document_id=%s AND chunk_index >= %s;",
            (document_id, len(chunks)),
        )
        return changed

    async def _embed_chunks(self, chunks: list[tuple[int, str]]) -> int:
        embedder = self.embedder
        if embedder is None:
            return 0
        embedded = 0
        for start in range(0, len(chunks), self.embedding_batch_size):
            batch = chunks[start : start + self.embedding_batch_size]
            vectors = await asyncio.to_thread(
                embedder.embed_texts, [text for _, text in batch]
            )
            async with self.pool.connection() as connection, connection.transaction():
                for (chunk_id, _), vector in zip(batch, vectors, strict=True):
                    await connection.execute(
                        """INSERT INTO recommendation_chunk_embeddings (
                            chunk_id,provider,model,dimensions,embedding,
                            embedding_version,embedded_at
                        ) VALUES (%s,%s,%s,%s,%s::vector,%s,now())
                        ON CONFLICT (chunk_id,provider,model,dimensions) DO UPDATE SET
                            embedding=EXCLUDED.embedding,
                            embedding_version=EXCLUDED.embedding_version,
                            embedded_at=now();""",
                        (
                            chunk_id,
                            embedder.config.provider,
                            embedder.config.model,
                            embedder.config.dimensions,
                            vector_literal(vector),
                            embedder.config.version,
                        ),
                    )
                    embedded += 1
        return embedded

    async def _upsert_external_document(
        self, document: Any
    ) -> tuple[bool, list[tuple[int, str]]]:
        async with self.pool.connection() as connection, connection.transaction():
            existing = await (
                await connection.execute(
                    """SELECT id,content_hash FROM recommendation_documents
                    WHERE source='waterloo_work' AND source_job_id=%s FOR UPDATE;""",
                    (document.source_job_id,),
                )
            ).fetchone()
            if existing and existing["content_hash"] == document.content_hash:
                return False, []
            inserted_document = await (
                await connection.execute(
                    """INSERT INTO recommendation_documents (
                        source,source_job_id,public_job_id,content_hash,title,role_family,
                        normalized_skills,requirement_tags,document_text,
                        metadata,index_version,indexed_at
                    ) VALUES ('waterloo_work',%s,NULL,%s,%s,%s,%s,%s,%s,%s,%s,now())
                    ON CONFLICT (source,source_job_id) DO UPDATE SET
                        content_hash=EXCLUDED.content_hash,title=EXCLUDED.title,
                        role_family=EXCLUDED.role_family,
                        normalized_skills=EXCLUDED.normalized_skills,
                        requirement_tags=EXCLUDED.requirement_tags,
                        document_text=EXCLUDED.document_text,metadata=EXCLUDED.metadata,
                        index_version=EXCLUDED.index_version,indexed_at=now()
                    RETURNING id;""",
                    (
                        document.source_job_id,
                        document.content_hash,
                        document.title,
                        document.role_family,
                        document.normalized_skills,
                        document.requirement_tags,
                        document.document_text,
                        Jsonb(document.metadata),
                        DOCUMENT_VERSION,
                    ),
                )
            ).fetchone()
            document_id = inserted_document["id"]
            chunks = await self._sync_chunks(connection, document_id, document.chunks)
        return True, chunks
