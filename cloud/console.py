"""Timestamped stdout logging shared by every cloud stage (GitHub Actions reads stdout)."""

from __future__ import annotations

from datetime import datetime


def log(message: str, level: str = "INFO") -> None:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] [{level}] {message}", flush=True)
