"""Resolve packaged resources and all writable per-user desktop paths."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DesktopPaths:
    user_data: Path
    resources: Path

    @property
    def logs(self) -> Path:
        return self.user_data / "logs"

    @property
    def runtime(self) -> Path:
        return self.user_data / "runtime"

    @property
    def backups(self) -> Path:
        return self.user_data / "backups"

    @property
    def models(self) -> Path:
        return self.user_data / "models"

    @property
    def waterlooworks(self) -> Path:
        return self.user_data / "waterlooworks"

    @property
    def waterlooworks_database(self) -> Path:
        return self.waterlooworks / "waterlooworks.sqlite3"

    @property
    def waterlooworks_chrome_profile(self) -> Path:
        return self.waterlooworks / "chrome-profile"

    @property
    def migrations(self) -> Path:
        return self.resources / "migrations"

    @property
    def collection_plan(self) -> Path:
        return self.resources / "config" / "collection_plans.json"

    @property
    def web(self) -> Path:
        return self.resources / "web"

    @classmethod
    def from_env(cls) -> DesktopPaths:
        user_data = os.getenv("WCFI_USER_DATA_DIR")
        resources = os.getenv("WCFI_RESOURCE_DIR")
        if not user_data or not resources:
            raise RuntimeError(
                "WCFI_USER_DATA_DIR and WCFI_RESOURCE_DIR are required in desktop mode"
            )
        return cls(Path(user_data).expanduser().resolve(), Path(resources).resolve())

    def prepare(self) -> None:
        for path in (
            self.user_data,
            self.logs,
            self.runtime,
            self.backups,
            self.models,
            self.waterlooworks,
        ):
            path.mkdir(parents=True, exist_ok=True)
            if os.name != "nt":
                path.chmod(0o700)

    def apply_environment(self) -> None:
        """Point existing modules at writable desktop-owned locations."""

        self.prepare()
        os.environ["WCFI_LOG_DIR"] = str(self.logs)
        os.environ["WCFI_RUNTIME_DIR"] = str(self.runtime)
        os.environ["WCFI_WEB_DIR"] = str(self.web)
        os.environ["WATERLOOWORKS_DB_PATH"] = str(self.waterlooworks_database)
        os.environ["WATERLOOWORKS_CHROME_PROFILE"] = str(self.waterlooworks_chrome_profile)
        os.environ.setdefault("HF_HOME", str(self.models / "huggingface"))
        os.environ.setdefault("XDG_CACHE_HOME", str(self.user_data / "cache"))
        os.environ.setdefault("INTERVIEW_TTS_BACKEND", "local")
