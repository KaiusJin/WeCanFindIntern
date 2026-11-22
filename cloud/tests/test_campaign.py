"""Campaign result contract consumed by the publisher and ops report."""

from datetime import UTC, datetime

from cloud import campaign


def test_campaign_result_payload_is_json_ready() -> None:
    result = campaign.CampaignResult(
        completed_at=datetime(2026, 9, 1, tzinfo=UTC),
        duration_seconds=12.345,
        status="partial",
        unique_jobs_collected=8,
        database_stats={"created": 6, "merged": 2, "updated": 0, "unchanged": 0},
        salary_stats={"structured": 1, "regex": 2, "not_found": 0, "failed": 0},
        recruiting_term_stats={
            "regex": 3,
            "not_found": 4,
            "cached": 0,
            "failed": 1,
        },
        query_stats={"retried": 0, "succeeded": 2, "failed": 1, "skipped": 4},
        failures=("indeed:403",),
    )

    payload = result.payload()
    assert payload["status"] == "partial"
    assert payload["failure_count"] == 1
    assert payload["database_stats"]["created"] == 6
    assert payload["duration_seconds"] == 12.35
