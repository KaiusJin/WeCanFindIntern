"""Per-collector job counts and SimplifyJobs recall for the operator report."""

from __future__ import annotations

from typing import Any

from cloud.sources.job_boards.simplify import SimplifyListing, coverage_report
from wecanfindintern.domain.normalized_job import NormalizedJob


def source_group(job: NormalizedJob) -> str:
    """Monitoring bucket: one per collector, not per underlying ATS."""
    via = (job.raw or {}).get("via")
    if via in {"stapply", "simplify"}:
        return via
    if job.source in {"greenhouse", "lever", "ashby", "workday"}:
        return f"ats_{job.source}"
    return job.source


def build_source_monitor(
    jobs: list[NormalizedJob], listings: list[SimplifyListing]
) -> dict[str, Any]:
    """Per-collector job counts and SimplifyJobs recall, before gap filling."""
    groups: dict[str, list[NormalizedJob]] = {}
    for job in jobs:
        groups.setdefault(source_group(job), []).append(job)
    monitor: dict[str, Any] = {
        "sources": {name: {"jobs": len(items)} for name, items in sorted(groups.items())}
    }
    if listings:
        monitor["coverage"] = coverage_report(listings, jobs)
        for name, items in groups.items():
            report = coverage_report(listings, items, sample=0)
            monitor["sources"][name]["simplify_covered"] = report["covered"]
            monitor["sources"][name]["simplify_recall"] = report["recall"]
    return monitor
