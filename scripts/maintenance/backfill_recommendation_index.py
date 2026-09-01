#!/usr/bin/env python3
"""Build or refresh recommendation RAG documents for all local job sources."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict

from wecanfindintern.agent.recommend.embeddings import EmbeddingConfig, EmbeddingGateway
from wecanfindintern.agent.recommend.indexer import (
    RecommendationIndexer,
    load_waterloo_jobs_from_sqlite,
)
from wecanfindintern.config import Settings
from wecanfindintern.db.pool import Database


async def run(*, limit: int | None, lexical_only: bool) -> None:
    database = Database(Settings.from_env())
    await database.open()
    try:
        config = None if lexical_only else EmbeddingConfig.from_env()
        if not lexical_only and config is None:
            raise RuntimeError(
                "Configure RECOMMEND_EMBEDDING_PROVIDER/model/key/base or pass "
                "--lexical-only"
            )
        # Keep document refresh independent from embedding calls. Passing an
        # embedder into the document pass makes every changed job issue its own
        # HTTP request; the second pass can send bounded batches instead.
        document_indexer = RecommendationIndexer(database.pool)
        public_report = await document_indexer.index_public_jobs(limit=limit)
        waterloo_items = await asyncio.to_thread(load_waterloo_jobs_from_sqlite, limit)
        waterloo_report = await document_indexer.index_waterloo_jobs(waterloo_items)
        embedding_report = None
        if config is not None:
            embedding_indexer = RecommendationIndexer(
                database.pool,
                embedder=EmbeddingGateway(config),
            )
            embedding_report = await embedding_indexer.embed_missing_primary_chunks(
                limit=limit
            )
        result = {
            field: getattr(public_report, field) + getattr(waterloo_report, field)
            for field in asdict(public_report)
        }
        if embedding_report is not None:
            result["chunks_embedded"] += embedding_report.chunks_embedded
            result["embedding_errors"] += embedding_report.embedding_errors
        result["sources"] = {
            "public": asdict(public_report),
            "waterloo_work": asdict(waterloo_report),
        }
        if embedding_report is not None:
            result["embedding"] = asdict(embedding_report)
        if config is not None:
            result["embedding_profile"] = config.version
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--lexical-only",
        action="store_true",
        help="Build full-text documents and chunks without calling an embedding API.",
    )
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    asyncio.run(run(limit=args.limit, lexical_only=args.lexical_only))


if __name__ == "__main__":
    main()
