"""Database repository implementations."""

from wecanfindintern.db.repositories.jobs import (
    IngestionOutcome,
    IngestionRun,
    JobIngestionRepository,
)
from wecanfindintern.db.repositories.recruiting_term import (
    RecruitingTermCandidate,
    RecruitingTermRepository,
)
from wecanfindintern.db.repositories.salary import (
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
