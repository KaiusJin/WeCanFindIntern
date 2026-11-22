#!/usr/bin/env python3
"""Refresh the bundled fallback ATS board registry.

Every campaign already discovers boards at run time from the SimplifyJobs lists
and the Stapply dataset, so this script is optional: it only refreshes the
committed fallback used when those sources are unreachable. Existing rows are
never removed.

    PYTHONPATH=src python -m cloud.discover_boards
"""

from __future__ import annotations

import argparse
from pathlib import Path

from cloud.config import DEFAULT_BOARDS_PATH
from cloud.sources.job_boards.boards import (
    load_boards,
    merge_boards,
    save_boards,
)
from cloud.sources.job_boards.providers import Scope
from cloud.sources.job_boards.simplify import fetch_simplify

REGISTRY = DEFAULT_BOARDS_PATH


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    args = parser.parse_args()

    existing = load_boards(args.registry) if args.registry.exists() else []
    snapshot = fetch_simplify(Scope(include_new_grad=True))
    merged = merge_boards(existing, snapshot.boards)
    save_boards(
        args.registry,
        merged,
        note=(
            "Fallback ATS boards. Campaigns also discover boards at run time from the "
            "SimplifyJobs lists and the Stapply dataset. Refresh with "
            "python -m cloud.discover_boards; "
            "add companies by hand as "
            "{provider, token, company}."
        ),
    )
    print(f"registry: {len(existing)} -> {len(merged)} boards (+{len(merged) - len(existing)})")


if __name__ == "__main__":
    main()
