"""Desktop background synchronization persists feed results and failures."""

from __future__ import annotations

import asyncio
import json

from wecanfindintern.desktop.job_sync_service import BackgroundJobSyncService


def test_scheduler_persists_sync_result(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WCFI_JOB_SYNC_ENABLED", "1")

    async def fake_sync(manifest_url, package_directory):
        assert manifest_url == "https://jobs.example/manifest.json"
        assert package_directory == tmp_path / "job-packages"
        return {"status": "success", "version": 7}

    monkeypatch.setattr("wecanfindintern.desktop.job_sync_service.sync_feed", fake_sync)
    service = BackgroundJobSyncService(
        manifest_url="https://jobs.example/manifest.json",
        lock_path=tmp_path / "job-sync.lock",
    )
    asyncio.run(service._run_once())

    assert service.status.running is False
    assert service.status.last_error is None
    assert service.status.last_result == {"status": "success", "version": 7}
    assert service.status.run_count == 1
    persisted = json.loads(service.state_path.read_text(encoding="utf-8"))
    assert persisted["last_result"]["version"] == 7


def test_scheduler_records_sync_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WCFI_JOB_SYNC_ENABLED", "1")

    async def fake_sync(*_args):
        raise RuntimeError("feed unavailable")

    monkeypatch.setattr("wecanfindintern.desktop.job_sync_service.sync_feed", fake_sync)
    service = BackgroundJobSyncService(
        manifest_url="https://jobs.example/manifest.json",
        lock_path=tmp_path / "job-sync.lock",
    )
    asyncio.run(service._run_once())

    assert service.status.last_result["status"] == "failed"
    assert "feed unavailable" in service.status.last_error
    assert service.status.run_count == 0
