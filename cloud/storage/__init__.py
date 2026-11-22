"""Cloud collection persistence and enrichment repositories."""

from cloud.storage.jobs import (
    IngestionOutcome,
    IngestionRun,
    JobIngestionRepository,
)
from cloud.storage.recruiting_term import (
    RecruitingTermCandidate,
    RecruitingTermRepository,
)
from cloud.storage.salary import (
    SalaryEnrichmentCandidate,
    SalaryRepository,
)

__all__ = [
    "IngestionOutcome",
    "IngestionRun",
    "JobIngestionRepository",
    "RecruitingTermCandidate",
    "RecruitingTermRepository",
    "SalaryEnrichmentCandidate",
    "SalaryRepository",
]
