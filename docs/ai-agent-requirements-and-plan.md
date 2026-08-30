# AI Agent Requirements and Design Context

The current runtime implementation is documented in [AI Agent and Memory](modules/ai-agent.md). This file retains the original product/architecture context and records the invariants that the implementation must preserve.

## Product boundary

The Agent is a natural-language workspace for searching jobs, inspecting Profile and Tracker state, recommending opportunities, preparing Profile changes, and performing a small set of confirmed Tracker/Profile writes. It is not an autonomous browser operator, arbitrary SQL console, email sender, or automatic job applicant.

## Implemented tool surface

Read-only tools are `get_profile`, `search_jobs`, `get_job_details`, `list_tracker`, `recommend_jobs`, `propose_profile_update`, and `generate_interview_questions`. Confirmed write tools are `add_interested`, `update_tracker_stage`, `remove_interested`, and `update_profile`.

The common job reference preserves source identity: public jobs use a public UUID; WaterlooWorks jobs use a WaterlooWorks Job ID. The Agent must resolve natural-language job names through search and must ask the user to choose when the match is ambiguous.

## State and persistence requirements

Agent sessions, messages, tool calls, approvals, and audit entries are stored in PostgreSQL. A write plan persists the exact validated arguments and preview before the user decides. Approval execution reads those original arguments and changes the approval only while it is pending, preventing double execution.

Agent memory consists of a token-bounded recent window, rolling summaries, typed long-term records, and explicit user preferences. Summary and extraction coverage watermarks make maintenance incremental. Long-term memory can be disabled through the `LONG_TERM_MEMORY` preference.

## Safety requirements

- Every write is previewed and requires explicit confirmation.
- Profile writes use a partial payload and field-level diff.
- Duplicate Interested/bookmark operations are idempotent.
- Repositories, not the model, own SQL and business rules.
- Public jobs and WaterlooWorks jobs retain separate storage and source identities.
- Passwords, MFA values, cookies, API keys, and arbitrary SQL are excluded from Agent input/state.
- Provider failures, missing records, invalid arguments, and ambiguous matches remain distinguishable to the user.

## API contract

The Agent routes live under `/api/v1/agent` and cover sessions, messages, pending approvals, decisions, memory status, preferences, and memory deletion. Provider/model/key validation occurs before a planning call. Read errors are returned as tool failures; LLM configuration/transport errors are returned as model failures; approval races return a conflict.

## Extension rules

New tools must declare a validated Pydantic argument model, a bounded output, a clear read/write classification, an idempotency rule, a repository/service implementation, audit behavior, and frontend rendering. New writes must retain the approval path. Cross-source tools must preserve public UUID vs. WaterlooWorks Job ID identity rather than copying records across databases.
