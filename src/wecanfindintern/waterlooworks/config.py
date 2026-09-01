"""Shared filesystem configuration for WaterlooWorks local state."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_WATERLOOWORKS_DATA_DIR = Path.home() / ".wecanfindintern"
DEFAULT_WATERLOOWORKS_DB_PATH = (
    DEFAULT_WATERLOOWORKS_DATA_DIR / "waterlooworks.sqlite3"
)
DEFAULT_WATERLOOWORKS_PROFILE_PATH = (
    DEFAULT_WATERLOOWORKS_DATA_DIR / "chrome-waterlooworks"
)

WATERLOOWORKS_ORIGIN = "https://waterlooworks.uwaterloo.ca"
WATERLOOWORKS_BOARDS: tuple[tuple[str, str], ...] = (
    ("full_cycle", f"{WATERLOOWORKS_ORIGIN}/myAccount/co-op/full/jobs.htm"),
    (
        "employer_student_direct",
        f"{WATERLOOWORKS_ORIGIN}/myAccount/co-op/direct/jobs.htm",
    ),
    ("graduating", f"{WATERLOOWORKS_ORIGIN}/myAccount/graduating/jobs.htm"),
    ("contract", f"{WATERLOOWORKS_ORIGIN}/myAccount/contract/jobs.htm"),
    ("campus", f"{WATERLOOWORKS_ORIGIN}/myAccount/campus/jobs.htm"),
)


def waterlooworks_database_path() -> Path:
    return Path(
        os.getenv("WATERLOOWORKS_DB_PATH", str(DEFAULT_WATERLOOWORKS_DB_PATH))
    ).expanduser()


def waterlooworks_profile_path() -> Path:
    return Path(
        os.getenv("WATERLOOWORKS_CHROME_PROFILE", str(DEFAULT_WATERLOOWORKS_PROFILE_PATH))
    ).expanduser()
