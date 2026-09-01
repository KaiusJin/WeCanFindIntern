"""Compatibility exports for public API job contracts."""

from wecanfindintern.application.job_models import (
    FacetCount,
    JobDetail,
    JobFacetsResponse,
    JobListFilters,
    JobListItem,
    JobPage,
    JobSourceResponse,
    LocationResponse,
    RecruitingTermResponse,
    SalaryResponse,
    decode_cursor,
    encode_cursor,
)

__all__ = [
    "FacetCount",
    "JobDetail",
    "JobFacetsResponse",
    "JobListFilters",
    "JobListItem",
    "JobPage",
    "JobSourceResponse",
    "LocationResponse",
    "RecruitingTermResponse",
    "SalaryResponse",
    "decode_cursor",
    "encode_cursor",
]
