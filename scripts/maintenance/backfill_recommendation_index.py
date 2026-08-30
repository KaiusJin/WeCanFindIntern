#!/usr/bin/env python3
"""Build or refresh recommendation RAG documents for active public jobs."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict

from wecanfindintern.agent.recommend.embeddings import EmbeddingConfig, EmbeddingGateway
from wecanfindintern.agent.recommend.indexer import RecommendationIndexer
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
        indexer = RecommendationIndexer(
            database.pool,
            embedder=EmbeddingGateway(config) if config is not None else None,
        )
        report = await indexer.index_public_jobs(limit=limit)
        if config is not None:
            embedding_report = await indexer.embed_missing_chunks(limit=limit)
            report.chunks_embedded += embedding_report.chunks_embedded
            report.embedding_errors += embedding_report.embedding_errors
        result = asdict(report)
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
