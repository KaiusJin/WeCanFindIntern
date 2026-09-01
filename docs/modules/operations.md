# Operations and Verification Module

## Local setup

Requirements: Python 3.10+, Node.js for syntax checks, Docker for PostgreSQL, and Chrome for WaterlooWorks.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
docker compose up -d postgres
cp .env.example .env
set -a
source .env
set +a
PYTHONPATH=src .venv/bin/python scripts/maintenance/migrate.py
```

Set `DATABASE_URL` in `.env`. `docker-compose.yml` exposes the development database only on localhost and persists it in the `wecanfindintern_postgres` volume.

## Run the application

```bash
PYTHONPATH=src .venv/bin/uvicorn wecanfindintern.api.app:app --reload
```

Open `http://127.0.0.1:8000/`. `/health` performs a database connectivity check.

## Collection operations

Inspect a provider response:

```bash
PYTHONPATH=src .venv/bin/python scripts/dev/inspect_jobspy_output.py \
  --site indeed --search-term "software engineer intern" \
  --location "Toronto, ON" --country-indeed Canada --results-wanted 5
```

Write raw and normalized development output:

```bash
PYTHONPATH=src .venv/bin/python scripts/dev/scrape_jobs.py \
  --site indeed --search-term "software engineer intern" \
  --location "Toronto, ON" --country-indeed Canada \
  --results-wanted 20 --output-dir data/raw
```

Run one database ingest:

```bash
PYTHONPATH=src .venv/bin/python scripts/collection/ingest_jobspy_to_db.py \
  --site indeed --search-term "software engineer intern" \
  --location "Toronto, ON" --country-indeed Canada --results-wanted 100
```

Run the configured campaign:

```bash
PYTHONPATH=src .venv/bin/python scripts/collection/run_collection_campaign.py
```

The campaign uses a single-instance lock, retries source failures, and executes collection before persistence/enrichment. Logs include query counts, retries, failures, created/merged/unchanged rows, and enrichment results.

## macOS launchd

The supplied plist schedules a campaign every four hours. Install it with:

```bash
mkdir -p logs ~/Library/LaunchAgents
cp config/launchd/com.kaius.wecanfindintern.collector.plist \
  ~/Library/LaunchAgents/com.kaius.wecanfindintern.collector.plist
launchctl bootstrap gui/$(id -u) \
  ~/Library/LaunchAgents/com.kaius.wecanfindintern.collector.plist
```

Inspect state and logs:

```bash
launchctl print gui/$(id -u)/com.kaius.wecanfindintern.collector
tail -f logs/collector.log
tail -f logs/collector-error.log
```

## Windows Task Scheduler

The Windows equivalent of the supplied `launchd` job is Task Scheduler. From
PowerShell, after installing the Python dependencies and creating `.env`, run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
& .\scripts\collection\register_windows_task.ps1
```

The task runs the campaign immediately after registration and then every four
hours. It writes to `logs\collector.log` and `logs\collector-error.log`. To
remove it:

```powershell
& .\scripts\collection\register_windows_task.ps1 -Unregister
```

The campaign lock is cross-platform: Unix uses `fcntl` and Windows uses the
native `msvcrt` lock, so manual and scheduled campaigns cannot overlap on
either platform.

The Windows runner expects the virtual environment at `.venv\Scripts\python.exe`.
It loads the same project `.env` file as the macOS runner and also runs the
recruiting-term backfill after the main campaign.

## Maintenance scripts

- `migrate.py`: apply numbered SQL migrations;
- `backfill_job_classification.py`: recalculate classification fields and version;
- `backfill_recruiting_terms.py`: run recruiting-term enrichment over existing jobs;
- `repair_salary_anomalies.py`: find/fix salary records violating current normalization expectations;
- `migrate_waterlooworks_to_sqlite.py`: migrate local WaterlooWorks data;
- `export_schemas.py`: export versioned public JSON schemas;
- `verify_data_api_contract.py`: validate public data contract;
- `verify_frontend_api_contract.py`: verify front-end route references.

## Local Ollama on Windows

Ollama is platform-neutral from the application's perspective. Install Ollama
for Windows, start its local service, and pull the configured models before
registering the scheduled task:

```powershell
ollama pull qwen3-embedding:0.6b
```

The default endpoints remain `http://localhost:11434` for embeddings and
`http://localhost:11434/v1` for chat-compatible requests. Keep
`RECOMMEND_EMBEDDING_PROVIDER=Ollama`; no remote API key is required. If
Ollama is not running, collection still completes, but recommendation indexing
or Ollama-powered AI requests will report an unavailable-provider error.

Raw snapshots are time-partitioned. Operators should create future partitions, monitor the default partition, use `VACUUM (ANALYZE)`, inspect slow queries with `EXPLAIN (ANALYZE, BUFFERS)`, and apply a retention policy by detaching/dropping expired partitions.

## Verification

```bash
PYTHONPATH=src .venv/bin/python -m pytest
make check
git diff --check
```

`make check` runs Ruff, pytest, front-end/API contract verification, and Node syntax checks. The tests cover domain normalization/classification, JobSpy adapter behavior, enrichment, repositories/routes, Profile security/parser, Tracker, WaterlooWorks, Agent tools/orchestrator, and Agent memory.

## Operational failure interpretation

- `/health` failure means PostgreSQL or pool configuration is unavailable.
- Campaign query failure is source-scoped; inspect retry and failure lines before treating the whole campaign as failed.
- A successful collection with zero jobs is valid for an empty page; a logged JobSpy error with zero jobs is retryable.
- A partial WaterlooWorks run means one or more boards failed while others may have imported successfully; inspect per-board counts.
- LLM feature errors are provider/configuration/output errors and do not indicate database corruption.
- Contract-check failures indicate route/frontend drift and should be fixed before merging a feature change.
