"""In-process TTL cache for recommendation results.

The cache key fingerprints everything that changes the answer: profile
revision, tracked-job set (exclusions), explicit preferences, active library
version, and the request arguments. WaterlooWorks content is not fingerprinted
(collection is manual and rare); the short TTL covers it.
"""

from __future__ import annotations

import hashlib
import time
from copy import deepcopy
from typing import Any

DEFAULT_TTL_SECONDS = 600.0
DEFAULT_MAX_ENTRIES = 128


def build_cache_key(
    *,
    profile_updated_at: str | None,
    tracked_fingerprint: str,
    preferences: dict[str, str],
    library_version: str,
    limit: int,
    source: str,
    request_filters: dict[str, Any],
    embedding_profile: str,
    llm_profile: str,
    use_semantic_retrieval: bool,
    use_llm_rerank: bool,
    exclude_tracked: bool,
) -> str:
    parts = (
        profile_updated_at or "",
        tracked_fingerprint,
        "\x1f".join(f"{k}={v}" for k, v in sorted(preferences.items())),
        library_version,
        str(limit),
        source,
        repr(sorted(request_filters.items())),
        embedding_profile,
        llm_profile,
        str(use_semantic_retrieval),
        str(use_llm_rerank),
        str(exclude_tracked),
    )
    return hashlib.sha256("\x1e".join(parts).encode("utf-8")).hexdigest()


class RecommendationCache:
    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._entries: dict[str, tuple[float, dict[str, Any]]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, payload = entry
        if time.monotonic() - stored_at > self.ttl_seconds:
            self._entries.pop(key, None)
            return None
        return deepcopy(payload)

    def put(self, key: str, payload: dict[str, Any]) -> None:
        now = time.monotonic()
        expired = [
            item_key
            for item_key, (stored_at, _) in self._entries.items()
            if now - stored_at > self.ttl_seconds
        ]
        for item_key in expired:
            self._entries.pop(item_key, None)
        if len(self._entries) >= self.max_entries:
            oldest = min(self._entries, key=lambda item_key: self._entries[item_key][0])
            self._entries.pop(oldest, None)
        self._entries[key] = (now, deepcopy(payload))

    def clear(self) -> None:
        self._entries.clear()


recommendation_cache = RecommendationCache()
