# Cloud collector

A batch pipeline that GitHub Actions runs on a schedule as a set of parallel
jobs (`cloud/parallel.py`, `.github/workflows/cloud-jobs.yml`). `python -m cloud.run`
still runs the whole campaign in one process (local/manual runs).
It is not part of the desktop app: `desktop/build/backend.spec` excludes it, and
`src/wecanfindintern` never imports it. It reuses `wecanfindintern.domain` as a
shared kernel.

```text
cloud/
├── run.py              Entry point: campaign (or --from-artifacts) → maintenance → publish → ops report
├── parallel.py         Entry point: plan / discover / collect shards for the parallel workflow
├── discover_boards.py  Entry point: refresh config/job_boards.json
├── campaign.py         Orchestrates planning, both source stages and the pipeline
├── console.py          Timestamped stdout logging
├── config/             collection_plans.json, job_boards.json
├── planning/           Plan expansion, query locations, sweep cadence
├── sources/
│   ├── jobspy/         Indeed + LinkedIn (derived from JobSpy, MIT) and stage.py
│   └── job_boards/     Greenhouse / Lever / Ashby / Workday, SimplifyJobs, Stapply, and stage.py
├── pipeline/           Persist + dedupe, then salary and recruiting-term enrichment
├── storage/            PostgreSQL repositories, checkpoint, lock and retention cleanup
├── publish/            Versioned R2 feed
├── ops/                Run report, DB stats, source monitor, dashboard
└── tests/              Mirrors the stage directories
```

Dependencies point one way:

```text
run      → campaign, storage, publish, ops
campaign → planning, sources, pipeline, storage, ops.monitor
sources  → planning.location_query, storage.collection_cache, wecanfindintern.domain
pipeline → storage
Only run imports campaign; nothing imports run.
```

Each source package owns a `stage.py` that turns its upstream into
`NormalizedJob` records; `campaign.py` only sequences stages. Add a new source
as `sources/<name>/` with its own `stage.py` and tests under `tests/sources/`.

Third-party code and data are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Parallel workflow

```text
plan ─┬─ indeed ──────────────────────────────────────┐
      └─ linkedin × search term ── linkedin-details ──┤
discover ─┬─ boards (GH/Lever/Ashby) ─────────────────┼─ publish
          └─ workday × pod slice ─────────────────────┘
```

| Job | Command | Database / secrets |
|---|---|---|
| `plan` | `python -m cloud.parallel plan work/plan` | Read-only `DATABASE_URL` for the sweep window and the LinkedIn IDs seen in the last 24 h (`linkedin_seen_window_seconds`) |
| `discover` | `python -m cloud.parallel discover work/discover` | None. Registry + SimplifyJobs + Stapply → `boards.json`, `stapply.jsonl.gz`, `simplify.json` and the Workday matrix |
| `indeed` | `collect indeed --plan …` | None |
| `linkedin` × term | `collect linkedin <term-slug> --plan …` | None. Both countries for one search term. Cards whose ID was seen in the last 24 h are **discarded** (by ID only; content changes are ignored). Then JDs for the remaining cards |
| `linkedin-details` | `collect linkedin-details --plan … --inputs …` | None. After all LinkedIn shards: retries the JD of every LinkedIn job a shard returned without one. Same waits; budget `linkedin_details_budget_seconds`. If this step is missing, the jobs are still published (soft failure) |
| `boards` | `collect boards --plan … --discover …` | None. Budget `ats.boards_budget_seconds` |
| `workday` × shard | `collect workday <shard> --plan … --discover …` | None. One shard per Workday pod; pods above 250 boards are split by a CRC32 of the board token, small pods share `misc`. Budget `ats.workday_shard_budget_seconds` |
| `publish` | `python -m cloud.run --from-artifacts artifacts` | `DATABASE_URL`, R2 |

- Every collector shard writes `jobs.jsonl.gz` + `result.json` (stats, failures, seconds) as an artifact.
- `publish` merges all shards (best record per fingerprint, so a JD found by `linkedin-details` replaces the shard's copy without one), then runs SimplifyJobs gap filling, persistence, maintenance, publishing and the ops report. The ops report lists every shard.
- A shard that failed, timed out or was cancelled leaves no artifact. It is reported as a failure, the run is `partial`, and everything else is published.
- Retention does not depend on what a run saw: jobs move to `jobs_archive` four months after they were first written (`cloud/storage/maintenance.py`).
- Because each runner has its own egress IP, LinkedIn and Workday rate limits apply per shard instead of to the whole campaign.

