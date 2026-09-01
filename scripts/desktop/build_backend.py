#!/usr/bin/env python3
"""Build the native PyInstaller sidecar for the current platform/architecture."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def target_name() -> str:
    system = {"Darwin": "darwin", "Windows": "win32"}.get(platform.system())
    if system is None:
        raise RuntimeError("Desktop release builds currently support macOS and Windows")
    machine = platform.machine().lower()
    architecture = "arm64" if machine in {"arm64", "aarch64"} else "x64"
    return f"{system}-{architecture}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    output_root = project_root / "desktop" / "resources" / "backend" / target_name()
    work_root = project_root / "build" / "pyinstaller" / target_name()
    if args.clean:
        shutil.rmtree(output_root, ignore_errors=True)
        shutil.rmtree(work_root, ignore_errors=True)
    output_root.parent.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--distpath",
        str(output_root),
        "--workpath",
        str(work_root),
        str(project_root / "packaging" / "backend.spec"),
    ]
    environment = os.environ.copy()
    environment["PYINSTALLER_CONFIG_DIR"] = str(work_root / "cache")
    subprocess.run(command, cwd=project_root, env=environment, check=True)
    executable = (
        output_root
        / "wecanfindintern-backend"
        / ("wecanfindintern-backend.exe" if sys.platform == "win32" else "wecanfindintern-backend")
    )
    if not executable.is_file():
        raise RuntimeError(f"PyInstaller did not produce {executable}")
    print(executable)


if __name__ == "__main__":
    main()
