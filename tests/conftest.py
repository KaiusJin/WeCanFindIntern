"""Ensure integration tests can import their shared helpers without a package."""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent / "integration"))
