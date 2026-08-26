#!/usr/bin/env python3
"""Validate and upsert database-backed JobSpy collection plans."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from wecanfindintern.config import Settings
from wecanfindintern.db.pool import Database
from wecanfindintern.ingestion.jobspy_adapter import SUPPORTED_SITES, JobSpyQuery
from wecanfindintern.ingestion.collection_catalog import expand_collection_catalog
from wecanfindintern.ingestion.location_query import resolve_query_location
from wecanfindintern.scheduler.repository import CollectionRepository


class PlanDefinition(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    enabled: bool = True
    sites: list[str]
    query: dict[str, Any]
    interval_seconds: int = Field(default=14_400, ge=300)
    page_size: int = Field(default=25, ge=1, le=100)
    max_results_per_source: int = Field(default=200, ge=1, le=1_000)
    max_attempts: int = Field(default=5, ge=1, le=10)

    @field_validator("sites")
    @classmethod
    def validate_sites(cls, sites: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(site.strip().lower() for site in sites))
        if not normalized:
            raise ValueError("sites cannot be empty")
        unknown = sorted(set(normalized) - SUPPORTED_SITES)
        if unknown:
            raise ValueError(f"unsupported sites: {', '.join(unknown)}")
        return normalized

    def validate_queries(self) -> None:
        for site in self.sites:
            values = dict(self.query)
            source_overrides = values.pop("source_overrides", {})
            values.update(source_overrides.get(site, {}))
            values = resolve_query_location(values, site)
            JobSpyQuery.model_validate(
                {
                    **values,
                    "sites": [site],
                    "offset": 0,
                    "results_wanted": self.page_size,
                }
            )


async def seed(path: Path) -> int:
    raw = json.loads(path.read_text(encoding="utf-8"))
    definitions = [
        PlanDefinition.model_validate(item) for item in expand_collection_catalog(raw)
    ]
    database = Database(Settings.from_env())
    await database.open()
    try:
        repository = CollectionRepository(database.pool)
        for definition in definitions:
            definition.validate_queries()
            await repository.upsert_plan(definition.model_dump(mode="json"))
        await repository.disable_plans_except([definition.name for definition in definitions])
    finally:
        await database.close()
    return len(definitions)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/collection_plans.json"),
    )
    args = parser.parse_args()
    count = asyncio.run(seed(args.config))
    print(f"已写入 {count} 个采集计划")


if __name__ == "__main__":
    main()
