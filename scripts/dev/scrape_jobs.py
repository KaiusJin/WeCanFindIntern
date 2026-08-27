#!/usr/bin/env python3
"""Collect jobs with JobSpy and persist raw CSV plus normalized JSONL."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from _cli import add_query_arguments, query_from_args

from wecanfindintern.ingestion.jobspy_adapter import scrape_and_normalize


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_query_arguments(parser)
    parser.add_argument("--output-dir", type=Path, default=Path("data/raw"))
    args = parser.parse_args()

    query = query_from_args(args)
    frame, result = scrape_and_normalize(query)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    source_slug = "-".join(query.sites)
    stem = f"{timestamp}_{source_slug}"
    csv_path = args.output_dir / f"{stem}_jobspy_raw.csv"
    jsonl_path = args.output_dir / f"{stem}_normalized.jsonl"

    frame.to_csv(
        csv_path,
        index=False,
        quoting=csv.QUOTE_NONNUMERIC,
        escapechar="\\",
    )
    with jsonl_path.open("w", encoding="utf-8") as file:
        for job in result.jobs:
            file.write(json.dumps(job.model_dump(mode="json"), ensure_ascii=False) + "\n")

    print(f"抓取完成: {result.raw_row_count} 条")
    print(f"JobSpy 原始 CSV: {csv_path}")
    print(f"内部标准 JSONL: {jsonl_path}")


if __name__ == "__main__":
    main()
