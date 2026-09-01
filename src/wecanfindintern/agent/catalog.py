"""Declarative Agent tool catalog, independent from tool implementations."""

from __future__ import annotations

from typing import Any

from wecanfindintern.agent.models import (
    AddInterestedArgs,
    GenerateInterviewQuestionsArgs,
    GetJobDetailsArgs,
    ListTrackerArgs,
    ProposeProfileUpdateArgs,
    RecommendJobsArgs,
    RemoveInterestedArgs,
    SearchJobsArgs,
    UpdateProfileArgs,
    UpdateTrackerStageArgs,
)

TOOL_CATALOG: list[dict[str, Any]] = [
    {
        "name": "get_profile",
        "description": (
            "Read the user's confirmed Profile (basics, education, work, projects, "
            "skills, certifications, languages, awards)."
        ),
        "parameters": {"type": "object", "properties": {}},
        "mutates": False,
    },
    {
        "name": "search_jobs",
        "description": (
            "Search jobs across the public library and WaterlooWorks. Returns title, "
            "company, location, source and job id."
        ),
        "parameters": SearchJobsArgs.model_json_schema(),
        "mutates": False,
    },
    {
        "name": "get_job_details",
        "description": (
            "Get full details for one job. Use job_id from search results; source is "
            "'public' for UUIDs or 'waterloo_work' for WaterlooWorks Job IDs."
        ),
        "parameters": GetJobDetailsArgs.model_json_schema(),
        "mutates": False,
    },
    {
        "name": "list_tracker",
        "description": "List Tracker records, optionally filtered by stage or query.",
        "parameters": ListTrackerArgs.model_json_schema(),
        "mutates": False,
    },
    {
        "name": "recommend_jobs",
        "description": (
            "Recommend jobs with hybrid RAG recall over Profile, job descriptions and "
            "preferences, followed by deterministic evidence scoring and an optional "
            "bounded LLM review. Never writes user data."
        ),
        "parameters": RecommendJobsArgs.model_json_schema(),
        "mutates": False,
    },
    {
        "name": "propose_profile_update",
        "description": (
            "Draft structured Profile changes from a user request. Read-only; returns "
            "a field-level draft with evidence and confidence."
        ),
        "parameters": ProposeProfileUpdateArgs.model_json_schema(),
        "mutates": False,
    },
    {
        "name": "generate_interview_questions",
        "description": (
            "Generate mock interview questions for one job. Resolve the job with "
            "search_jobs/get_job_details first and pass job_id plus source, or pass a "
            "raw job_description. Read-only."
        ),
        "parameters": GenerateInterviewQuestionsArgs.model_json_schema(),
        "mutates": False,
    },
    {
        "name": "add_interested",
        "description": (
            "Plan to add one or more jobs to the Tracker's Interested stage. Requires "
            "user confirmation before it runs."
        ),
        "parameters": AddInterestedArgs.model_json_schema(),
        "mutates": True,
    },
    {
        "name": "update_tracker_stage",
        "description": (
            "Plan to change one or more Tracker records to a new stage (interested, "
            "applied, interview, offer, rejected). Requires user confirmation."
        ),
        "parameters": UpdateTrackerStageArgs.model_json_schema(),
        "mutates": True,
    },
    {
        "name": "remove_interested",
        "description": (
            "Plan to remove one or more jobs from Interested. Records past Interested "
            "are protected and will not be removed. Requires user confirmation."
        ),
        "parameters": RemoveInterestedArgs.model_json_schema(),
        "mutates": True,
    },
    {
        "name": "update_profile",
        "description": (
            "Plan to save a full profile.v1 payload to the user's Profile. Requires "
            "user confirmation; a field-level diff is shown first."
        ),
        "parameters": UpdateProfileArgs.model_json_schema(),
        "mutates": True,
    },
]
