#!/usr/bin/env python3
"""Validate and stage a PostgreSQL 16 + pgvector runtime for Electron Forge."""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
from pathlib import Path


def target_name() -> str:
    system = {"Darwin": "darwin", "Windows": "win32"}.get(platform.system())
    if system is None:
        raise RuntimeError("Desktop release builds currently support macOS and Windows")
    machine = platform.machine().lower()
    architecture = "arm64" if machine in {"arm64", "aarch64"} else "x64"
    return f"{system}-{architecture}"


def executable(name: str) -> str:
    return f"{name}.exe" if platform.system() == "Windows" else name


def validate(source: Path) -> None:
    required = [
        source / "bin" / executable(name)
        for name in (
            "postgres",
            "initdb",
            "pg_ctl",
            "pg_isready",
            "createdb",
            "pg_dump",
            "pg_restore",
            "dropdb",
            "psql",
        )
    ]
    extension_directories = (
        source / "share" / "extension",
        source / "share" / "postgresql@16" / "extension",
    )
    for extension in ("vector", "pgcrypto", "pg_trgm"):
        controls = tuple(directory / f"{extension}.control" for directory in extension_directories)
        if not any(path.is_file() for path in controls):
            required.append(controls[0])
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("PostgreSQL runtime is incomplete:\n" + "\n".join(missing))
    vector_control = next(
        directory / "vector.control"
        for directory in extension_directories
        if (directory / "vector.control").is_file()
    )
    version = vector_control.read_text(encoding="utf-8")
    if "default_version" not in version:
        raise RuntimeError("pgvector vector.control is invalid")
    postgres_version = subprocess.run(
        [source / "bin" / executable("postgres"), "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if " 16." not in postgres_version:
        raise RuntimeError(f"PostgreSQL 16 is required, got: {postgres_version.strip()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Portable runtime root containing bin/, lib/ and share/",
    )
    parser.add_argument("--target", default=target_name())
    args = parser.parse_args()

    source = args.source.expanduser().resolve()
    validate(source)
    project_root = Path(__file__).resolve().parents[2]
    destination = project_root / "desktop" / "resources" / "postgres" / args.target
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True, exist_ok=True)
    for directory_name in ("bin", "lib", "share"):
        source_directory = source / directory_name
        if not source_directory.is_dir():
            raise RuntimeError(f"PostgreSQL runtime is missing {source_directory}")
        shutil.copytree(
            source_directory,
            destination / directory_name,
            symlinks=False,
        )
    validate(destination)
    print(destination)


if __name__ == "__main__":
    main()
