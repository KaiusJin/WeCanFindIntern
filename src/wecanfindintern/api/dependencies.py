"""Shared FastAPI dependency providers.

Route modules consume these providers but never import providers from one
another, keeping the API composition layer acyclic.
"""

from fastapi import Request

from wecanfindintern.db.read_repository import JobReadRepository
from wecanfindintern.profile.repository import ProfileRepository
from wecanfindintern.tracker.repository import TrackerRepository


def get_job_repository(request: Request) -> JobReadRepository:
    return JobReadRepository(request.app.state.database.pool)


def get_profile_repository(request: Request) -> ProfileRepository:
    return ProfileRepository(request.app.state.database.pool)


def get_tracker_repository(request: Request) -> TrackerRepository:
    return TrackerRepository(request.app.state.database.pool)
