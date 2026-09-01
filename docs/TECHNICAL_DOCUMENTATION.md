# WeCanFindIntern Technical Documentation

## 1. Document purpose

This is the system-wide technical document for the current WeCanFindIntern implementation. It explains the final runtime architecture and how the modules cooperate. Detailed behavior is split into the module documents under [`docs/modules/`](modules/README.md); the legacy contract filenames are retained and linked from [`docs/README.md`](README.md).

The implementation is a local job-search and application workspace. It collects public jobs through JobSpy, normalizes them into a stable internal model, stores and deduplicates them in PostgreSQL, exposes a versioned FastAPI data API, serves a static browser UI, supports a dedicated WaterlooWorks browser workflow, and provides Profile, Tracker, AI career tools, and a guarded AI Agent.

## 2. Repository map

```text
WeCanFIndIntern/
├── src/wecanfindintern/
│   ├── api/                 FastAPI application, models, and route groups
│   ├── agent/               AI Agent, tools, persistence, and memory
│   ├── ats/                 ATS review service and models
│   ├── cover_letter/        Cover-letter generation and export
│   ├── db/                  PostgreSQL pool, read repository, write repositories
│   ├── domain/              Canonical job, normalization, classification, salary, term
│   ├── ingestion/           JobSpy adapter, catalog, location queries, enrichment
│   ├── interview/           Mock interview, answer analysis, and TTS
│   ├── llm/                 Provider gateway and prompt templates
│   ├── profile/             Profile model, repository, parser, and file security
│   ├── tracker/             Application model and repository
│   └── waterlooworks/       Chrome session, collector, extractor, SQLite repository
├── web/                     Static HTML, CSS, and native ES modules
├── migrations/              Ordered PostgreSQL schema migrations
├── schemas/                 Versioned public job JSON Schemas
├── scripts/                 Collection, maintenance, development, and verification CLIs
├── config/                  Collection catalog and launchd configuration
├── vendor/JobSpy/           Audited local JobSpy dependency
└── tests/                   Unit, route, repository, integration-contract, and memory tests
```

## 3. Runtime architecture

### 3.1 Processes and storage

The application is one FastAPI process with two persistence systems:

```text
Browser
  │ REST /api/v1/...
  ▼
FastAPI application
  ├── PostgreSQL async pool
  │     ├── public jobs and sources
  │     ├── raw snapshots and ingestion metadata
  │     ├── Profile and resume imports
  │     ├── Application Tracker and events
  │     └── Agent sessions, approvals, audit, and memory
  │
  └── WaterlooWorksService
        ├── dedicated Chrome profile/debug session
        └── local WaterlooWorks SQLite database
```

PostgreSQL is the source of truth for public jobs and cross-feature application data. WaterlooWorks remains local and isolated because it depends on a user-authenticated browser session and has a different source identity/lifecycle.

### 3.2 Application lifecycle

`wecanfindintern.api.app:create_app()` registers all API routers and mounts `web/` as a static application. The FastAPI lifespan:

1. Loads `DATABASE_URL`, pool settings, and statement timeout through `Settings.from_env()`.
2. Opens the asynchronous PostgreSQL pool.
3. Creates `WaterlooWorksService`, which initializes its SQLite repository, Chrome session, and latest-run snapshot.
4. Serves requests.
5. On shutdown, cancels a running WaterlooWorks task, closes the Chrome connection, and closes PostgreSQL.

API routes are registered before the static mount, so `/api/...` paths cannot be captured by the HTML fallback. `/health` executes `SELECT 1` against the configured pool.

## 4. End-to-end public job flow

```text
collection_plans.json
        ↓ expand_collection_catalog
source/location query definitions
        ↓ JobSpy
pandas DataFrame
        ↓ stabilize_jobspy_frame / dataframe_to_records
NormalizedJob
        ↓ canonical_job_from_jobspy
CanonicalJobInput
        ↓ batch repository ingest
source idempotency + candidate generation + dedupe decision
        ↓
PostgreSQL jobs/job_sources/raw_job_snapshots
        ↓ post-dedupe enrichment
salary + recruiting term
        ↓
GET /api/v1/jobs, /facets, /{id}
        ↓
web/modules/jobs.js
```

The important boundary is the separation of source data, canonical business data, and public API data:

- `NormalizedJob` is stable relative to JobSpy and contains a diagnostic raw row.
- `CanonicalJobInput` contains normalized company, location, compensation, classification, tags, source identity, and deduplication keys.
- PostgreSQL stores current canonical fields and separate source/snapshot history.
- `job.v3` list items, `job-detail.v4` details, and `job-page.v3` pages expose versioned public shapes without provider payloads.

## 5. Data processing rules

### 5.1 Source normalization

The ingestion adapter fixes the JobSpy column contract even for zero-result DataFrames, converts NaN/date/URL/list values, and captures source failures that JobSpy only logs. A successful empty page and a failed empty page are therefore distinguishable.

### 5.2 Canonicalization

The domain layer normalizes text, URLs, dates, companies, locations, employment types, work mode, and salary. It derives classification, skills, requirements, display tags, description hashes, direct URL hashes, and company/location dedupe blocks. Missing data remains missing; the system does not fill unknown country, city, salary, or work mode values with guesses.

### 5.3 Classification

Classification is deterministic and versioned. Opportunity type and schedule are separate dimensions. The title supplies the primary role category; technologies and requirements extracted from the JD become skill/requirement tags and do not change the role category. `classification_version` makes rule changes backfillable.

### 5.4 Salary and recruiting term

Structured provider salary is preferred, then cached enrichment, deterministic regex, and finally the constrained DeepSeek fallback. Annualized salary is derived from the interval for cross-job comparison. Recruiting terms use the same regex-first/DeepSeek-fallback pattern and are cached by title/JD content hash.

Both enrichment stages run after the complete campaign has been ingested and deduplicated. This avoids spending model calls on duplicate records and lets one canonical job own the enrichment result.

## 6. Deduplication and concurrency

Deduplication is intentionally staged:

1. Source-level idempotency uses a SHA-256 `source_fingerprint` and unique source indexes.
2. Cross-source candidate generation uses direct URL hash, company/location block, and a publication-date window, capped at 25 candidates.
3. Candidate comparison uses direct URL, company, title, location/work mode, date distance, and 5-token description shingles.
4. A decision is persisted with score, matched rules, and algorithm version.

An automatic match attaches another source to the canonical job. A non-match keeps a separate job. There is no human review queue in this flow.

Ingestion transactions acquire advisory locks for the relevant fingerprint and dedupe block. This prevents concurrent campaigns from creating duplicate source rows or racing within the same block without serializing unrelated jobs.

## 7. API surface

### Public jobs

| Route | Behavior |
|---|---|
| `GET /health` | Database health check |
| `GET /api/v1/jobs` | Active job list with filters and keyset cursor |
| `GET /api/v1/jobs/{job_id}` | Canonical detail and source links |
| `GET /api/v1/jobs/facets` | Active-job counts for filter controls |

### Feature route groups

| Prefix | Module |
|---|---|
| `/api/v1/ats` | PDF extraction and ATS review |
| `/api/v1/interview` | Practice sessions, trend, TTS, questions, answer analysis |
| `/api/v1/cover-letter` | Generation and document export |
| `/api/v1/tracker` | Applications, bookmarks, events, bulk operations, CSV |
| `/api/v1/profile` | Profile, resumes, imports, and confirmation |
| `/api/v1/waterlooworks` | Browser state, collection, local jobs |
| `/api/v1/agent` | Sessions, messages, tools, approvals, memory, preferences |

FastAPI/Pydantic validates request shape and limits. The route layer creates repositories/services from `request.app.state`; domain and repository layers enforce the actual business behavior.

## 8. Separate WaterlooWorks flow

WaterlooWorks follows a second end-to-end path:

```text
launch dedicated Chrome
    → user completes SSO/MFA
    → service detects authenticated WaterlooWorks target
    → per board open URL + All Jobs
    → extract Job IDs and posting details
    → normalize salary/location/description
    → insert posting content once by Job ID; refresh observation timestamps only
    → update run/board snapshot
    → API polling and local UI rendering
```

The collector continues after an individual board failure. The snapshot distinguishes waiting-for-login, ready, collecting, completed/partial/failed, and closed-browser conditions. Raw posting payloads stay in SQLite and are stripped from API responses. The Tracker and Agent preserve `source=waterloo_work` so these records cannot be confused with public UUID jobs.

## 9. Profile, Tracker, and career tools

Profile provides one current `profile.v1` record rather than a version history,
plus a resume-to-draft-to-confirm workflow. PDF/LaTeX input is checked for type,
magic bytes/UTF-8, size, structure, active content, minimum text, and language
before parsing. The parser never compiles LaTeX. Normal edits autosave the current
record; imported fields autosave into `profile_imports.parsed_payload` and mutate
the current profile only when the user confirms Apply.

Tracker stores current application state and event history. Public jobs, WaterlooWorks Job IDs, and custom entries have separate source identity. Bookmark writes are idempotent; stage/field updates create events; bulk actions report per-operation results.

ATS parsing readiness and resume-job matching are deterministic, evidence-backed
scorers and do not use an AI provider. Cover letters use the shared LLM gateway
with a bounded Writer/Reviewer loop; interview answers are transcribed locally
with faster-whisper and analyzed as text with any provider; TTS returns audio
from gTTS. These services return user-facing `ok/error` results rather than
leaking provider stack traces.

## 10. AI Agent architecture

The Agent adds a stateful conversational layer over the existing repositories:

```text
message
  → session/history/context/memory load
  → LLM JSON plan
  → read tool execution OR write preview
  → approval decision
  → original persisted write arguments
  → repository mutation + audit
  → assistant response
```

Reads execute immediately. Writes (`add_interested`, `update_tracker_stage`, `remove_interested`, `update_profile`) create an approval with validated arguments and a preview. Approval execution is conditional on the approval still being pending, making the operation one-shot.

Memory has four layers: token-bounded recent messages, rolling structured summaries, typed long-term records, and explicit preferences. Summary/extraction watermarks prevent reprocessing. Recall combines lexical similarity, confidence, recency, and token budgets. The `LONG_TERM_MEMORY=DISABLED` preference stops long-term memory use/extraction while leaving the active session available.

## 11. Frontend architecture

The browser is a native ES-module application without a build pipeline. `index.html` supplies the DOM; `styles.css` supplies layout/theme; modules own feature behavior. Shared helpers escape dynamic HTML, render Markdown, format dates/salary, install request timeouts, and configure file drop zones.

The main browser data patterns are:

- Jobs use facets plus keyset cursor pages and load details on demand.
- WaterlooWorks polls asynchronous status and queries local jobs separately.
- Tracker synchronizes filters to the URL and refreshes bookmark/application state after mutations.
- Profile edits autosave the single current profile. Import reviews autosave only
  their draft payload and do not replace the current profile until explicit Apply.
- AI settings are browser localStorage values sent per request; server-backed feature state remains in the databases.
- Agent UI renders assistant/tool/approval results and refreshes memory/preferences independently.

The front-end contract verifier scans API references against the FastAPI route table, and Node syntax checks run over all modules.

## 12. Reliability and safety model

- Source collection is concurrent but bounded, retried, and single-instance locked.
- Database statements have a configured timeout and connection pool bounds.
- Public job reads use active partial indexes and keyset pagination.
- Raw source payloads are isolated from public responses.
- Resume files have strict validation and no LaTeX execution path.
- Browser credentials remain in the dedicated interactive Chrome session.
- AI writes require explicit confirmation and use domain repositories.
- Model outputs are parsed/validated as JSON where required and bounded by retry/size rules.
- Audit records capture Agent tool intent, arguments summary, approval, result, and errors.

## 13. Change workflow

For a new source or field, update the source adapter, canonical model, persistence migration/repository, public Pydantic contract/schema, front-end consumer, and tests together. For a new feature, add its service/model/route, front-end module, dependency wiring, and contract tests. Derived classification/enrichment changes must preserve raw/source identity and provide a backfill path.

Run the complete checks before merging:

```bash
PYTHONPATH=src .venv/bin/python -m pytest
make check
git diff --check
```

See [Operations and Verification](modules/operations.md) for setup, launchd, collection, migration, maintenance, and failure interpretation.
