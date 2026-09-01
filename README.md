# WeCanFindIntern

WeCanFindIntern is a local job-search and application workspace for internship, co-op, new-grad, and related opportunities. It collects jobs from multiple public sources through a pinned vendored JobSpy integration, standardizes and deduplicates them in PostgreSQL, serves a versioned FastAPI API and static browser UI, imports WaterlooWorks postings through a dedicated interactive Chrome session, and provides Profile, Application Tracker, ATS, cover-letter, mock-interview, and guarded AI Agent workflows.

## What is included

- Multi-source JobSpy collection for Indeed, LinkedIn, Glassdoor, ZipRecruiter, and Google Jobs.
- Stable `NormalizedJob` and `CanonicalJobInput` boundaries independent of provider DataFrame shapes.
- Location hierarchy, work-mode, opportunity/schedule, category, skill, requirement, salary, and recruiting-term normalization.
- Source-level idempotency, cross-source deduplication, raw snapshots, and audited dedupe decisions.
- PostgreSQL public job API with cursor pagination and live facets.
- Dedicated WaterlooWorks Chrome profile, SSO/MFA handoff, five-board collection, local SQLite storage, and per-board progress.
- `profile.v1` candidate profile, secure English PDF/LaTeX import, reviewable import drafts, and confirmation.
- Application Tracker with public/WaterlooWorks/custom records, bookmarks, events, bulk actions, and CSV export.
- Provider gateway for Gemini, OpenAI, DeepSeek, GLM, Qwen, and Ollama.
- ATS review, grounded Writer/Reviewer cover letters with DOCX/PDF export, mock interview questions, local audio transcription, provider-agnostic answer analysis, and TTS.
- AI Agent sessions, read tools, approval-gated writes, audit records, rolling summaries, typed long-term memory, recall, and preferences.
- Static native ES-module frontend with job search, Tracker, Profile, WaterlooWorks, career tools, and Agent UI.

## Documentation

Start with the [system-wide technical documentation](docs/TECHNICAL_DOCUMENTATION.md), then use the independent module documents:

- [Documentation index](docs/README.md)
- [Job ingestion](docs/modules/job-ingestion.md)
- [Domain and normalization](docs/modules/domain-normalization.md)
- [Database and Data API](docs/modules/database-and-data-api.md)
- [WaterlooWorks](docs/modules/waterlooworks.md)
- [Profile and resume import](docs/modules/profile.md)
- [Application Tracker](docs/modules/tracker.md)
- [AI Agent and memory](docs/modules/ai-agent.md)
- [LLM-assisted career tools](docs/modules/llm-assisted-tools.md)
- [Frontend](docs/modules/frontend.md)
- [Operations and verification](docs/modules/operations.md)

Stable contract references remain available for JobSpy, the Data API, the job taxonomy, Profile, Agent design context, and migrations.

## Requirements

- Python 3.10 or newer
- Docker for the development PostgreSQL service
- Node.js for frontend syntax checks
- Chrome for WaterlooWorks import
- API keys for the selected remote AI provider; Ollama can run locally without a remote key

## Quick start

Create an environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Start PostgreSQL and configure the application:

```bash
docker compose up -d postgres
cp .env.example .env
set -a
source .env
set +a
PYTHONPATH=src .venv/bin/python scripts/maintenance/migrate.py
```

`DATABASE_URL` is required. The default Docker database is bound to `127.0.0.1:5432`; set a matching connection string in `.env`.

Start the API and browser UI:

```bash
PYTHONPATH=src .venv/bin/uvicorn wecanfindintern.api.app:app --reload
```

Open <http://127.0.0.1:8000/>. The health endpoint is <http://127.0.0.1:8000/health>.

## Collect jobs

Inspect an actual JobSpy response before a larger collection:

```bash
PYTHONPATH=src .venv/bin/python scripts/dev/inspect_jobspy_output.py \
  --site indeed \
  --search-term "software engineer intern" \
  --location "Toronto, ON" \
  --country-indeed Canada \
  --results-wanted 5
```

Save raw CSV and stable JSONL output:

```bash
PYTHONPATH=src .venv/bin/python scripts/dev/scrape_jobs.py \
  --site indeed \
  --search-term "software engineer intern" \
  --location "Toronto, ON" \
  --country-indeed Canada \
  --results-wanted 20 \
  --output-dir data/raw
```

Run one database ingest:

```bash
PYTHONPATH=src .venv/bin/python scripts/collection/ingest_jobspy_to_db.py \
  --site indeed \
  --search-term "software engineer intern" \
  --location "Toronto, ON" \
  --country-indeed Canada \
  --results-wanted 100
```

Run the configured campaign:

```bash
PYTHONPATH=src .venv/bin/python scripts/collection/run_collection_campaign.py
```

The campaign expands `config/collection_plans.json`, runs source queries concurrently with bounded retries, filters records outside the requested Canada/US scope, completes ingestion and deduplication, then performs salary and recruiting-term enrichment. Collection is single-instance locked so manual and scheduled runs do not overlap.

On Windows, use PowerShell Task Scheduler registration instead of the macOS
`launchd` plist:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
& .\scripts\collection\register_windows_task.ps1
```

This runs the same Python campaign immediately and every four hours. See
[`docs/modules/operations.md`](docs/modules/operations.md) for removal,
logging, and local Ollama setup.

## WaterlooWorks

Use the WaterlooWorks area in the browser UI to launch the dedicated Chrome profile. Complete Waterloo SSO/MFA in that window, wait for the connected/ready status, and start collection. The importer opens Full-Cycle, Employer-Student Direct, Graduating, Contract, and Campus boards, clicks `All Jobs`, imports each posting, and continues when one board fails.

WaterlooWorks data is stored in `~/.wecanfindintern/waterlooworks.sqlite3` by default and remains separate from public PostgreSQL jobs. Its dedicated Chrome profile is `~/.wecanfindintern/chrome-waterlooworks`. Override these paths with `WATERLOOWORKS_*` environment variables.

## Public API

Main job routes:

- `GET /api/v1/jobs`
- `GET /api/v1/jobs/{job_id}`
- `GET /api/v1/jobs/facets`

Feature prefixes:

```text
/api/v1/ats
/api/v1/interview
/api/v1/cover-letter
/api/v1/tracker
/api/v1/profile
/api/v1/waterlooworks
/api/v1/agent
```

Public job responses use `job.v3` list items, `job-detail.v4` details, `job-page.v3` pages, and `job-facets.v2` facets in `schemas/`. Source payloads are not returned by the public API.

## Tests and checks

```bash
PYTHONPATH=src .venv/bin/python -m pytest
make check
git diff --check
```

`make check` runs Ruff, the Python test suite, frontend-to-OpenAPI contract verification, and Node syntax checks for all frontend modules.

## Project conventions

- Keep JobSpy-specific assumptions in `ingestion/`.
- Use domain models instead of provider DataFrames or database rows in business logic.
- Preserve raw/source identity while making derived fields deterministic and versioned.
- Keep WaterlooWorks in its own storage and source namespace.
- Route all LLM calls through `llm/gateway.py`.
- Route Agent mutations through approval and existing domain repositories.
- Add migrations, schemas, frontend references, and tests together when a public contract changes.
- Follow source-site terms, rate limits, privacy requirements, and applicable law.
