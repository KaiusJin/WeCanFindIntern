#!/usr/bin/env python3
"""Refresh the committed fallback registry scripts/collection/ats_boards.json.

Every campaign already discovers boards at run time from the SimplifyJobs lists
and the Stapply dataset, so this script is optional: it only refreshes the
committed fallback used when those sources are unreachable. Existing rows are
never removed.

    PYTHONPATH=src python scripts/collection/discover_ats_boards.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from wecanfindintern.ingestion.ats.boards import load_boards, merge_boards, save_boards
from wecanfindintern.ingestion.ats.providers import Scope
from wecanfindintern.ingestion.ats.simplify import fetch_simplify

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "scripts/collection/ats_boards.json"


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
            "scripts/collection/discover_ats_boards.py; add companies by hand as "
            "{provider, token, company}."
        ),
    )
    print(f"registry: {len(existing)} -> {len(merged)} boards (+{len(merged) - len(existing)})")


if __name__ == "__main__":
    main()
