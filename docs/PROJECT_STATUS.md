# Current Project State

This is a concise implementation snapshot as of 2026-09-01. The detailed
technical reference is [Technical Documentation](TECHNICAL_DOCUMENTATION.md),
cross-cutting failure handling is [Reliability and Recovery](RELIABILITY_AND_RECOVERY.md),
and individual modules are indexed in [Documentation](README.md).

## Current product shape

```text
JobSpy collection
  → stable normalization
  → classification/location/salary/recruiting-term enrichment
  → PostgreSQL idempotency and cross-source deduplication
  → versioned job API and browser search
  → Profile, Tracker, WaterlooWorks, career tools, and AI Agent
```

The supported desktop shape is Electron + packaged Python/FastAPI + embedded
PostgreSQL 16. Public collection and WaterlooWorks intentionally use safe
restart-from-source semantics rather than persisted page checkpoints: database
identity, unique constraints, content hashes, and insert-once external IDs make
reruns idempotent.

## Implemented modules

| Area | Current behavior | Primary implementation |
|---|---|---|
| Public job collection | Indeed, LinkedIn, Glassdoor, ZipRecruiter, and Google Jobs through vendored JobSpy | `src/wecanfindintern/ingestion/`, `scripts/collection/` |
| Source boundary | Stable DataFrame columns, `NormalizedJob`, raw CSV/JSONL diagnostics, logged-error detection | `ingestion/jobspy_adapter.py` |
| Domain data | Canonical company/location/work-mode/salary/date model | `src/wecanfindintern/domain/` |
| Classification | Versioned opportunity, schedule, category, subcategory, skill, requirement, and display tags | `domain/classification.py` |
| Enrichment | Regex-first salary and recruiting-term extraction with cached DeepSeek JSON fallback | `ingestion/*_llm.py`, `ingestion/*_enrichment.py` |
| Persistence | PostgreSQL jobs, sources, snapshots, runs, dedupe decisions, status history | `src/wecanfindintern/db/`, `migrations/` |
| Public API | Active-job cursor feed, UUID detail, facets, versioned schemas | `api/app.py`, `api/models.py`, `schemas/` |
| WaterlooWorks | Dedicated Chrome session, SSO/MFA handoff, five boards, local SQLite, board progress | `src/wecanfindintern/waterlooworks/` |
| Profile | Structured `profile.v1`, secure English PDF/LaTeX import, draft/confirm workflow | `src/wecanfindintern/profile/` |
| Tracker | Public/WaterlooWorks/custom applications, bookmarks, stages, events, bulk actions, CSV | `src/wecanfindintern/tracker/` |
| AI career tools | ATS, cover letter Writer/Reviewer, interview questions/analysis, TTS | `src/wecanfindintern/ats/`, `cover_letter/`, `interview/` |
| AI Agent | Sessions, read tools, approval-gated writes, audit, memory, preferences | `src/wecanfindintern/agent/` |
| Frontend | Static HTML/CSS/native ES modules for all product areas | `web/` |
| Verification | Unit tests, route tests, contract checks, Ruff, JS syntax checks, diff/links review | `tests/`, `Makefile` |

## Data and safety invariants

- Provider DataFrames never cross the ingestion boundary.
- Raw source payloads are stored separately from public canonical records.
- Public jobs use PostgreSQL UUIDs; WaterlooWorks uses local external Job IDs.
- Cross-source dedupe never merges WaterlooWorks with public jobs.
- Derived classifications and enrichments are versioned/cacheable and can be backfilled.
- Agent writes require explicit confirmation and reuse existing repositories.
- Resume files are validated before parsing; LaTeX is never executed.
- Browser SSO/MFA secrets remain inside the dedicated Chrome session.
- Desktop API traffic is loopback-only and token-protected; renderer secrets use
  the OS secure store through the preload bridge.

## Verification snapshot

The repository test suite was re-run during this documentation update:

```text
269 passed, 2 failed, 7 skipped
```

The two failures are existing implementation/test drift outside the documentation
changes (`profile` parser IDs and a WaterlooWorks application-row compatibility
field). `make check` is also currently blocked by an existing unused import in
`src/wecanfindintern/db/read_repository.py`. The frontend/API contract verifier,
all frontend Node syntax checks, Markdown relative-link check, and
`git diff --check` pass. Resolve the code/test drift before claiming the full
repository gate is green.

## Primary files

- [Root README](../README.md)
- [System-wide technical documentation](TECHNICAL_DOCUMENTATION.md)
- [Module documentation index](README.md)
- [Collection configuration](../config/collection_plans.json)
- [FastAPI application](../src/wecanfindintern/api/app.py)
- [JobSpy adapter](../src/wecanfindintern/ingestion/jobspy_adapter.py)
- [Canonical job model](../src/wecanfindintern/domain/jobs.py)
- [Classification rules](../src/wecanfindintern/domain/classification.py)
- [Public schemas](../schemas/)
- [Collection campaign](../scripts/collection/run_collection_campaign.py)
- [Desktop sidecar](../src/wecanfindintern/desktop/server.py)
- [Reliability and recovery](RELIABILITY_AND_RECOVERY.md)
