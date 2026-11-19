#!/usr/bin/env python3
"""Bundle non-system dylibs and validate a portable macOS PostgreSQL runtime."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from collections import deque
from pathlib import Path

PORTABLE_BUILD_PREFIX = "/opt/wecanfindintern/postgresql16/"


def is_macho(path: Path) -> bool:
    if not path.is_file():
        return False
    result = subprocess.run(["file", "-b", str(path)], capture_output=True, text=True, check=True)
    return "Mach-O" in result.stdout


def dependencies(path: Path) -> list[str]:
    output = subprocess.run(
        ["otool", "-L", str(path)], capture_output=True, text=True, check=True
    ).stdout
    return [
        line.strip().split(" (compatibility", 1)[0]
        for line in output.splitlines()[1:]
        if line.strip()
    ]


def bundled_reference(target: Path, dependency: Path) -> str:
    relative = os.path.relpath(dependency, target.parent)
    return f"@loader_path/{relative}"


def minimum_macos_version(path: Path) -> tuple[int, ...] | None:
    output = subprocess.run(
        ["otool", "-l", str(path)], capture_output=True, text=True, check=True
    ).stdout
    lines = iter(output.splitlines())
    for line in lines:
        if line.strip() == "cmd LC_BUILD_VERSION":
            for command_line in lines:
                stripped = command_line.strip()
                if stripped.startswith("minos "):
                    return tuple(int(part) for part in stripped.split()[1].split("."))
                if stripped.startswith("cmd "):
                    break
        if line.strip() == "cmd LC_VERSION_MIN_MACOSX":
            for command_line in lines:
                stripped = command_line.strip()
                if stripped.startswith("version "):
                    return tuple(int(part) for part in stripped.split()[1].split("."))
                if stripped.startswith("cmd "):
                    break
    return None


def display_version(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


def version_is_newer(version: tuple[int, ...], reference: tuple[int, ...]) -> bool:
    width = max(len(version), len(reference))
    return version + (0,) * (width - len(version)) > reference + (0,) * (width - len(reference))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    parser.add_argument("--deployment-target", default="13.0")
    args = parser.parse_args()
    runtime = args.runtime.expanduser().resolve()
    deployment_target = tuple(int(part) for part in args.deployment_target.split("."))
    portable_lib = runtime / "lib" / "portable"
    portable_lib.mkdir(parents=True, exist_ok=True)

    queue = deque(
        path
        for root in (runtime / "bin", runtime / "lib")
        for path in root.rglob("*")
        if is_macho(path)
    )
    seen: set[Path] = set()
    copied: dict[str, Path] = {}
    source_for_target: dict[Path, Path] = {}
    while queue:
        target = queue.popleft()
        if target in seen:
            continue
        seen.add(target)
        for dependency_name in dependencies(target):
            if dependency_name.startswith("/opt/homebrew/"):
                source = Path(dependency_name).resolve()
            elif dependency_name.startswith("@loader_path/") and target in source_for_target:
                relative = dependency_name.removeprefix("@loader_path/")
                source = (source_for_target[target].parent / relative).resolve()
                if not source.is_file():
                    continue
            else:
                continue
            # Preserve the referenced basename (including version aliases), not
            # the resolved symlink basename expected only inside Homebrew.
            destination = portable_lib / Path(dependency_name).name
            existing = copied.get(source.name)
            if existing is not None and existing.resolve() != source:
                raise RuntimeError(
                    f"Two Homebrew libraries share the basename {source.name}: "
                    f"{existing} and {source}"
                )
            copied[source.name] = source
            if not destination.exists():
                shutil.copy2(source, destination)
                destination.chmod(0o755)
                source_for_target[destination] = source
                queue.append(destination)

    for target in sorted(seen | set(portable_lib.iterdir())):
        if not is_macho(target):
            continue
        for old_reference in dependencies(target):
            if old_reference.startswith("/opt/homebrew/"):
                dependency = portable_lib / Path(old_reference).name
            elif old_reference.startswith(PORTABLE_BUILD_PREFIX):
                dependency = runtime / old_reference.removeprefix(PORTABLE_BUILD_PREFIX)
            else:
                continue
            subprocess.run(
                [
                    "install_name_tool",
                    "-change",
                    old_reference,
                    bundled_reference(target, dependency),
                    str(target),
                ],
                check=True,
            )
        # otool reports a dylib's own install name as its first entry. Rewrite
        # PostgreSQL's original libraries too, otherwise that metadata still
        # points at the Homebrew prefix even after every consumer is portable.
        if target.suffix == ".dylib":
            subprocess.run(
                ["install_name_tool", "-id", f"@loader_path/{target.name}", str(target)],
                check=True,
            )

    unresolved: list[str] = []
    for target in seen | set(portable_lib.iterdir()):
        if is_macho(target):
            for item in dependencies(target):
                if item.startswith(("/opt/homebrew/", PORTABLE_BUILD_PREFIX)):
                    unresolved.append(f"{target}: {item}")
                elif item.startswith("@loader_path/"):
                    relative = item.removeprefix("@loader_path/")
                    if not (target.parent / relative).resolve().is_file():
                        unresolved.append(f"{target}: missing {item}")
    if unresolved:
        raise RuntimeError("Unresolved Homebrew dependencies:\n" + "\n".join(unresolved))

    incompatible: list[str] = []
    for target in seen | set(portable_lib.iterdir()):
        if not is_macho(target):
            continue
        minimum = minimum_macos_version(target)
        if minimum is not None and version_is_newer(minimum, deployment_target):
            incompatible.append(f"{target}: macOS {display_version(minimum)}")
    if incompatible:
        expected = display_version(deployment_target)
        raise RuntimeError(
            f"Mach-O files require a macOS version newer than {expected}:\n"
            + "\n".join(sorted(incompatible))
        )

    # install_name_tool invalidates Homebrew's original signatures. Ad-hoc sign
    # each rewritten image so it can run during local packaging and smoke tests;
    # the finished Electron app is signed again by the release pipeline.
    for target in sorted(seen | set(portable_lib.iterdir())):
        if is_macho(target):
            subprocess.run(["codesign", "--force", "--sign", "-", str(target)], check=True)
    print(
        f"Bundled {len(copied)} non-system libraries and validated macOS "
        f"{display_version(deployment_target)} compatibility in {portable_lib}"
    )


if __name__ == "__main__":
    main()
