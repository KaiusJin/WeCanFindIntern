"""Desktop scheduler status must preserve partial and failed campaign outcomes."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from scripts.collection.run_collection_campaign import CampaignResult
from wecanfindintern.desktop.collection import BackgroundCollectionService


def _result(status: str = "partial") -> CampaignResult:
    return CampaignResult(
        completed_at=datetime.now(UTC),
        duration_seconds=5.0,
        status=status,
        unique_jobs_collected=12,
        database_stats={"created": 10, "merged": 2, "updated": 0, "unchanged": 0},
        salary_stats={"structured": 0, "regex": 0, "deepseek": 0},
        recruiting_term_stats={
            "regex": 0,
            "deepseek": 0,
            "not_found": 0,
            "cached": 0,
            "failed": 0,
        },
        query_stats={"retried": 0, "succeeded": 3, "failed": 1, "skipped": 2},
        failures=("provider unavailable",),
    )


def test_scheduler_persists_partial_result(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WCFI_BACKGROUND_COLLECTION_ENABLED", "1")

    async def fake_run(*_args, **_kwargs):
        return _result()

    monkeypatch.setattr("wecanfindintern.desktop.collection.run", fake_run)
    service = BackgroundCollectionService(
        config_path=tmp_path / "collection.json",
        lock_path=tmp_path / "collection.lock",
    )

    asyncio.run(service._run_once())

    assert service.status.running is False
    assert service.status.last_error is None
    assert service.status.last_result["status"] == "partial"
    assert service.status.last_result["database_stats"]["created"] == 10
    assert service.status.run_count == 1
    persisted = json.loads(service.state_path.read_text(encoding="utf-8"))
    assert persisted["last_result"]["query_stats"]["skipped"] == 2


def test_scheduler_records_fatal_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WCFI_BACKGROUND_COLLECTION_ENABLED", "1")

    async def fake_run(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("wecanfindintern.desktop.collection.run", fake_run)
    service = BackgroundCollectionService(
        config_path=tmp_path / "collection.json",
        lock_path=tmp_path / "collection.lock",
    )

    asyncio.run(service._run_once())

    assert service.status.last_result["status"] == "failed"
    assert "database unavailable" in service.status.last_error
    assert service.status.run_count == 0
