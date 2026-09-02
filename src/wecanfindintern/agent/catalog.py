"""Declarative Agent tool catalog, independent from tool implementations."""

from __future__ import annotations

from typing import Any

from wecanfindintern.agent.models import (
    AddIntoTrackerArgs,
    AnalyseJobArgs,
    CompareJobsArgs,
    GenerateInterviewQuestionsArgs,
    GetJobDetailsArgs,
    ListTrackerArgs,
    ProposeProfileUpdateArgs,
    RecommendJobsArgs,
    RemoveTrackerArgs,
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
            "Search jobs across the public library and WaterlooWorks for exact filters "
            "or catalog lookup. Returns title, company, location, source and job id. "
            "The limit is a global maximum across all requested sources. Use "
            "recommend_jobs instead when the user wants roles suited to their Profile "
            "or stated preferences."
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
        "name": "analyse_job",
        "description": (
            "Deeply analyse one job's complete JD against the user's confirmed Profile. "
            "Extracts responsibilities and hiring priorities; separates must-have, "
            "preferred, and implicit requirements; assesses Profile evidence item by "
            "item; identifies skill, experience, and education gaps; checks seniority, "
            "work authorization, location, and deadline risks; and recommends whether "
            "the role is worth applying to. Never writes data."
        ),
        "parameters": AnalyseJobArgs.model_json_schema(),
        "mutates": False,
    },
    {
        "name": "compare_jobs",
        "description": (
            "Compare 2 to 5 explicit jobs against the user's confirmed Profile and "
            "saved preferences. Returns a ranked comparison, trade-offs, evidence "
            "quality, and the job that is the best overall fit. Use the source-aware "
            "job references from attached jobs or search results. Never writes data."
        ),
        "parameters": CompareJobsArgs.model_json_schema(),
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
            "bounded LLM review. The top two results, or the top one when only one "
            "exists, receive a full job analysis. Use this when the user asks for a set "
            "of jobs based on a preferred location, role, work mode, or other personal "
            "fit signal, even if they do not explicitly say 'recommend'. Never writes "
            "user data."
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
        "name": "add_into_tracker",
        "description": (
            "Add one or more jobs directly to the Tracker at the initial Interested "
            "stage. This action does not require confirmation."
        ),
        "parameters": AddIntoTrackerArgs.model_json_schema(),
        "mutates": False,
    },
    {
        "name": "update_tracker_stage",
        "description": (
            "Change one or more Tracker records to a new stage (interested, applied, "
            "interview, offer, rejected) immediately without confirmation."
        ),
        "parameters": UpdateTrackerStageArgs.model_json_schema(),
        "mutates": False,
    },
    {
        "name": "remove_from_tracker",
        "description": (
            "Plan to permanently remove one or more records from the Tracker, including "
            "records in Applied, Interview, Offer, or Rejected stages. Requires user "
            "confirmation."
        ),
        "parameters": RemoveTrackerArgs.model_json_schema(),
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
