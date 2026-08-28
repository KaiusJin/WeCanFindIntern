#!/usr/bin/env python3
"""Verify every frontend API reference exists in the OpenAPI route table.

This guards the exact failure class that broke the app before: a frontend
calling endpoints that no longer exist (or vice versa).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from wecanfindintern.api.app import app

ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT / "web"
MODULES_DIR = WEB_DIR / "modules"


def _frontend_refs() -> list[str]:
    """Return normalized /api/... references found in the web app."""

    js = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(MODULES_DIR.glob("*.js"))
    )
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    refs: list[str] = []

    for match in re.finditer(r"fetch\(\s*(`[^`]*`|\"[^\"]*\"|'[^']*')", js):
        url = match.group(1)[1:-1]
        if url.startswith("/api/"):
            refs.append(url)
    for match in re.finditer(r'href="(/api/[^"]+)"', html):
        refs.append(match.group(1))
    return refs


def _normalize(path: str) -> str:
    path = path.split("?", 1)[0]
    return re.sub(r"\$\{[^}]+\}", "*", path)


def main() -> int:
    openapi_paths = list(app.openapi()["paths"])
    failures: list[str] = []

    for ref in sorted(set(_frontend_refs())):
        normalized = _normalize(ref)
        segments = [
            "[^/]+" if segment == "*" else re.escape(segment)
            for segment in normalized.split("/")
        ]
        expected = re.compile("^" + "/".join(segments) + "$")
        if not any(expected.match(path) for path in openapi_paths):
            failures.append(ref)

    if failures:
        print("Frontend API references missing from the API:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    refs = _frontend_refs()
    print(f"OK: {len(refs)} frontend API references match the OpenAPI route table")
    return 0


if __name__ == "__main__":
    sys.exit(main())
