#!/usr/bin/env python3
"""Export versioned public job API JSON Schemas."""

from __future__ import annotations

import json
from pathlib import Path

from wecanfindintern.api.models import JobDetail, JobFacetsResponse, JobPage


def write_schema(model, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    write_schema(JobDetail, Path("schemas/job.v3.json"))
    write_schema(JobPage, Path("schemas/job-page.v3.json"))
    write_schema(JobFacetsResponse, Path("schemas/job-facets.v2.json"))
    print("已导出 job.v3、job-page.v3 和 job-facets.v2 JSON Schema")


if __name__ == "__main__":
    main()
