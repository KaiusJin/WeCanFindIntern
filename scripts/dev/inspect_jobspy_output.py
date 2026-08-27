#!/usr/bin/env python3
"""Run a small scrape and print JobSpy's actual DataFrame contract."""

from __future__ import annotations

import argparse
import json
from typing import Any

import pandas as pd
from wecanfindintern.ingestion.jobspy_cli import add_query_arguments, query_from_args

from wecanfindintern.ingestion.jobspy_adapter import scrape_and_normalize


def preview_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    text = str(value)
    return text if len(text) <= 240 else f"{text[:237]}..."


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_query_arguments(parser)
    args = parser.parse_args()
    frame, result = scrape_and_normalize(query_from_args(args))

    print(f"shape: {frame.shape}")
    print("columns:")
    for name in frame.columns:
        print(f"  - {name}: dtype={frame[name].dtype}, nulls={int(frame[name].isna().sum())}")

    if frame.empty:
        print("sample: null (本次没有抓到职位，但适配层已补齐固定列)")
        return

    sample = {key: preview_value(value) for key, value in frame.iloc[0].to_dict().items()}
    print("sample:")
    print(json.dumps(sample, ensure_ascii=False, indent=2))
    print(f"normalized_jobs: {len(result.jobs)}")


if __name__ == "__main__":
    main()
