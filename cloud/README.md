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
      └─ linkedin × term × country ── linkedin-details ┤
discover ─┬─ boards (GH/Lever/Ashby) ─────────────────┼─ merge ─ simplify-gap-fill ─ publish
          └─ workday × pod slice ─────────────────────┘
```

| Job | Command | Database / secrets |
|---|---|---|
| `plan` | `python -m cloud.parallel plan work/plan` | Read-only `DATABASE_URL` for the sweep window and the LinkedIn IDs seen in the last 24 h (`linkedin_seen_window_seconds`) |
| `discover` | `python -m cloud.parallel discover work/discover` | None. Registry + SimplifyJobs + Stapply → `boards.json`, `stapply.jsonl.gz`, `simplify.json` and the Workday matrix |
| `indeed` | `collect indeed --plan … --out out` | None |
| `linkedin` × term × country | `collect linkedin <country>-<term-slug> --plan … --out out` | None. One search term in one country (e.g. `us-software-engineer-intern`), up to `max_results_by_source.linkedin` (60) cards in pages of 10. Cards whose ID was seen in the last 24 h are **discarded** (by ID only; content changes are ignored). Then JDs for the remaining cards |
| `linkedin-details` | `collect linkedin-details --plan … --inputs … --out out` | None. After all LinkedIn shards: retries the JD of every LinkedIn job a shard returned without one. Same waits; budget `linkedin_details_budget_seconds`. If this step is missing, the jobs are still published (soft failure) |
| `boards` | `collect boards --plan … --discover … --out out` | None. Budget `ats.boards_budget_seconds` |
| `workday` × shard | `collect workday <shard> --plan … --discover … --out out` | CSC tunnel (optional). One shard per Workday pod; small pods share `misc`. Without the tunnel, pods above 250 boards are split by a CRC32 of the board token, budget `ats.workday_shard_budget_seconds`. With it, pods are not split (one shared IP), budget `ats.workday_single_egress_budget_seconds` |
| `merge` | `python -m cloud.parallel merge artifacts merged` | None. Runs even when collectors failed: best record per fingerprint, missing shards listed (hard), soft failures kept |
| `simplify-gap-fill` | `python -m cloud.parallel gap-fill merged gapfill` | None. SimplifyJobs coverage, gap-fill jobs and their JDs (`ats.simplify_detail_budget_seconds`) |
| `publish` | `python -m cloud.run --from-artifacts artifacts` | `DATABASE_URL`, R2. Dedupe + persist → 4-month archive → R2 feed → ops report. Runs even if `simplify-gap-fill` failed (coverage is then measured locally, soft failure) |

- Every collector shard writes `jobs.jsonl.gz` + `result.json` (stats, failures, seconds) as an artifact.
- `merge` keeps the best record per fingerprint, so a JD found by `linkedin-details` replaces the shard's copy without one. The ops report lists every shard, plus `merge` and `simplify-gap-fill`.
- `publish` ingests 250 records per transaction with batch source/candidate lookups and multi-row writes. Records within a batch still deduplicate against earlier records in that batch. Each committed batch logs progress, outcome counts and elapsed time.
- No failed collector blocks anything downstream. `merge` and `publish` run with `always()`. Only a failed `plan` (no `plan.json`) or a failed `merge` stops the run from publishing.
- A shard that failed, timed out or was cancelled leaves no artifact. It is reported as a failure, the run is `partial`, and everything else is published.
- Retention does not depend on what a run saw: jobs move to `jobs_archive` four months after they were first written (`cloud/storage/maintenance.py`).
- Because each runner has its own egress IP, LinkedIn rate limits apply per shard instead of to the whole campaign.
- Workday throttles GitHub-hosted runner IPs almost completely. When the repository
  secrets `CSC_SSH_HOST`, `CSC_SSH_USER`, `CSC_SSH_KEY` and `CSC_KNOWN_HOSTS` are set,
  the `workday` shards and `simplify-gap-fill` open an SSH SOCKS tunnel to the CSC
  server (`.github/actions/csc-tunnel`) and only their collect step uses it
  (`HTTPS_PROXY`). Without the secrets they run direct, as before.

