from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from wecanfindintern.ingestion.jobspy_adapter import JobSpyQuery
from wecanfindintern.scheduler import runner


def query() -> JobSpyQuery:
    return JobSpyQuery(sites=["indeed"], search_term="software engineer intern")


def test_scrape_checked_keeps_genuine_empty_result(monkeypatch) -> None:
    expected = SimpleNamespace(jobs=[])
    monkeypatch.setattr(
        runner,
        "scrape_and_normalize",
        lambda _: ("frame", expected),
    )

    assert runner.scrape_checked(query()) == ("frame", expected)


def test_scrape_checked_converts_jobspy_error_log_to_exception(monkeypatch) -> None:
    logger = logging.getLogger("JobSpy:Indeed")

    def failed_scrape(_):
        logger.error("upstream status code 403")
        return "frame", SimpleNamespace(jobs=[])

    monkeypatch.setattr(runner, "scrape_and_normalize", failed_scrape)

    with pytest.raises(RuntimeError, match="status code 403"):
        runner.scrape_checked(query())
