"""Content-addressed cache for LLM responses.

Keys are SHA-256 over provider + model + system prompt + user prompt, so
identical inputs return the stored response without a provider call. The
store is a synchronous psycopg connection: cache lookups run alongside the
(usually thread-pooled) LLM call, and misses simply fall through.
"""

from __future__ import annotations

import hashlib
import os

import psycopg

DEFAULT_TTL_DAYS = 7

_cache_dsn: str | None = None


def configure(database_url: str | None) -> None:
    """Point the cache at a database; ``None`` disables caching."""

    global _cache_dsn
    _cache_dsn = database_url


def cache_enabled() -> bool:
    return bool(_cache_dsn)


def cache_key(provider: str, model: str, system_prompt: str, user_prompt: str) -> str:
    digest = hashlib.sha256()
    for part in (provider, model, system_prompt, user_prompt):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


def lookup(key: str) -> str | None:
    """Return the cached response, or None on miss/disabled/expiry."""

    if not _cache_dsn:
        return None
    try:
        with psycopg.connect(_cache_dsn, connect_timeout=3) as connection:
            row = connection.execute(
                """
                SELECT response, created_at FROM llm_cache
                WHERE cache_key = %s
                  AND created_at > now() - %s::interval
                """,
                (key, os.getenv("LLM_CACHE_TTL", f"{DEFAULT_TTL_DAYS} days")),
            ).fetchone()
            return row[0] if row else None
    except psycopg.Error:
        return None


def store(key: str, provider: str, model: str, response: str) -> None:
    if not _cache_dsn:
        return
    try:
        with psycopg.connect(_cache_dsn, connect_timeout=3) as connection:
            connection.execute(
                """
                INSERT INTO llm_cache (cache_key, provider, model, response)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (cache_key) DO UPDATE SET
                    response = EXCLUDED.response,
                    created_at = now()
                """,
                (key, provider, model, response),
            )
            connection.commit()
    except psycopg.Error:
        return


def purge_expired() -> int:
    if not _cache_dsn:
        return 0
    try:
        with psycopg.connect(_cache_dsn, connect_timeout=3) as connection:
            cursor = connection.execute(
                "DELETE FROM llm_cache WHERE created_at < now() - %s::interval",
                (os.getenv("LLM_CACHE_TTL", f"{DEFAULT_TTL_DAYS} days"),),
            )
            connection.commit()
            return cursor.rowcount
    except psycopg.Error:
        return 0
