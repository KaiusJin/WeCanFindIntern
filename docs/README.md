# WeCanFindIntern Documentation

This directory contains the English technical documentation for the current implementation. The code is the source of truth; these documents explain the data contracts, processing order, persistence boundaries, failure behavior, and operator workflows implemented in the repository.

## Start here

| Document | Scope |
|---|---|
| [Technical Documentation](TECHNICAL_DOCUMENTATION.md) | System-wide architecture, request/data flows, boundaries, and cross-module rules |
| [Job Ingestion](modules/job-ingestion.md) | JobSpy adapter, collection campaign, retries, raw snapshots, and ingestion lifecycle |
| [Domain and Data Normalization](modules/domain-normalization.md) | Canonical job model, location, classification, salary, recruiting term, and deduplication inputs |
| [Database and Data API](modules/database-and-data-api.md) | PostgreSQL schema, repositories, migrations, query design, public API, pagination, and facets |
| [WaterlooWorks](modules/waterlooworks.md) | Dedicated Chrome session, SSO/MFA boundary, board collection, SQLite storage, and routes |
| [Profile and Resume Import](modules/profile.md) | Profile schema, PDF/LaTeX validation, extraction, import drafts, confirmation, and storage |
| [Application Tracker](modules/tracker.md) | Application stages, bookmarks, events, bulk actions, custom jobs, and CSV export |
| [AI Agent and Memory](modules/ai-agent.md) | Sessions, tool planning, approvals, tool contracts, audit, summaries, recall, and preferences |
| [ATS-Style Resume Diagnostics](modules/ats-review.md) | Deterministic parsing readiness, resume-job matching, evidence, formulas, and limits |
| [LLM-Assisted Career Tools](modules/llm-assisted-tools.md) | LLM gateway, cover letters, interview coaching, TTS, prompts, and provider behavior |
| [Frontend](modules/frontend.md) | Static application shell, ES modules, API integration, state, rendering, and browser storage |
| [Operations and Verification](modules/operations.md) | Setup, migrations, collection operations, launchd, maintenance scripts, tests, and checks |

## Existing contract references

The following documents remain the stable references for specific contracts and are aligned with the module documentation:

- [JobSpy Integration](JOBSPY_INTEGRATION.md)
- [Data API](DATA_API.md)
- [Job Data Taxonomy](JOB_DATA_TAXONOMY.md)
- [Profile](PROFILE.md)
- [AI Agent Requirements and Plan](ai-agent-requirements-and-plan.md)
- [Migrations](../migrations/README.md)

When a module document and an old planning note disagree, the implementation and the module document take precedence. Planning notes are retained for design context and must not be read as a list of missing runtime features.
