"""Bundled collection defaults, resolved independently of the working directory."""

from pathlib import Path

DEFAULT_PLAN_PATH = Path(__file__).with_name("collection_plans.json")
DEFAULT_BOARDS_PATH = Path(__file__).with_name("job_boards.json")
