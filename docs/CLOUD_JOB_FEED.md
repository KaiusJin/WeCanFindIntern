# Public job feed

The `Public job feed` workflow runs the existing JobSpy campaign at 00:17 and 12:17 UTC every day and accepts manual dispatches. The collector, its cache and the exporter use a private PostgreSQL schema. Desktop installations read a public HTTPS feed and serve jobs from their own PostgreSQL database.

## Provision once

1. Use the provisioned Supabase jobs project and its `job_collector` login. Keep `job_feed` outside the Data API exposed schemas. The collector uses this schema without creating tables or running DDL upgrades at startup. Cloud database provisioning SQL is not stored in this repository.

   Use the IPv4 **session pooler** DSN on port 5432, username `job_collector.<project-ref>`, URL-encoded password and `sslmode=require`. Session pooling supports the collector's advisory lock and prepared statements. [Supabase connection guide](https://supabase.com/docs/guides/database/connecting-to-postgres)

2. Create a dedicated **R2 Standard** bucket. Connect a custom domain in R2 and issue an Object Read & Write token scoped to this bucket. The custom domain serves public GETs. Bypass CDN caching for `/manifest.json` and allow caching of the immutable `/snapshots/*` and `/deltas/*` objects. Let the publisher manage expiration. [R2 public buckets](https://developers.cloudflare.com/r2/buckets/public-buckets/)

3. Configure repository Actions settings in WeCanFindIntern.

   | Kind | Name | Value |
   | --- | --- | --- |
   | Secret | `SUPABASE_DATABASE_URL` | Collector session DSN |
   | Secret | `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | Bucket-scoped key pair |
   | Variable | `R2_ENDPOINT_URL` | S3 API endpoint from the R2 account |
   | Variable | `R2_BUCKET` | Dedicated bucket name |
   | Variable | `WCFI_PUBLIC_JOBS_MANIFEST_URL` | Full custom-domain URL ending in `/manifest.json` |

4. After the workflow is available on the default branch, dispatch **Public job feed**. Check the job counts and version in the Actions log, then fetch the public manifest. Compare the next scheduled run with the manual run. GitHub schedules execute on the default branch. [GitHub workflow events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

5. Build the desktop release with the manifest variable configured. The release workflow embeds the public URL. Development and manual builds can set the same environment variable at launch or supply `desktop/resources/public-job-manifest.txt` before packaging. Disable any separately installed legacy launchd or Windows public collection task at cutover.

## Publication and recovery

Completed provider queries are checkpointed in compressed form during a sweep. A restarted runner restores matching queries for up to six hours. Batches already written to PostgreSQL use the collector's existing deduplication. A partial run publishes available changes and reports failure in Actions so the operator can investigate the affected providers.

The exporter compares hashes of canonical job and ordered source fields inside PostgreSQL. A sighting advances the collector's observation clocks. Only a changed projection advances the job's `sync_seq`. The on-device last-seen times reflect the observation included in the last distributed content change. An existing closed status is distributed as recorded. A complete successful campaign expires jobs last seen over 14 days ago. Closed and expired jobs are purged seven days after closure, generating tombstones. Raw history is pruned after one day except for the latest cached LinkedIn payload. Detailed deduplication decision history is kept for seven days. Collection enrichment uses regex only and requires no LLM API key.

Every changed version has a current full gzip snapshot. The runner assembles it from the preceding R2 snapshot plus database changes. Each manifest advertises SHA-256 over the compressed bytes, length, record count and the last 42 delta packages. Packages are uploaded before replacing the manifest. Cleanup keeps all referenced objects and skips unreferenced packages uploaded in the last 48 hours.

The desktop checks on startup, every four hours while running, and from the tray. It retries failures after five minutes. Each verified package and its cursor commit together. Partial downloads can resume by byte range. A missing delta or unsupported gap selects the current snapshot. Legacy public jobs are matched by source fingerprint to retain bookmark UUIDs. Removal archives a managed local row so existing applications and agent references survive. Profiles, applications, agent memory, WaterlooWorks credentials and its SQLite database stay on the device.

## Free-tier sizing and monitoring

The last 30 historical campaign summaries in the development collector log averaged 887 unique source records and 131 newly created jobs per run. At two runs a day, a 30-day month has 60 runs. The table gives a planning envelope. Row sizes, client population and provider response sizes are assumptions until the hosted run produces measurements.

| Resource | Example monthly load | Free allowance |
| --- | --- | --- |
| Supabase database | 5,502 retained jobs at 16 KiB each including sources/indexes, plus 100 MB for base tables, checkpoints and headroom ≈ 190 MB. This assumes 21 days from first observation to purge on average. | 500 MB/project |
| Supabase egress | 20 MB/run for all query results, including cache reads, enrichment and export ≈ 1.2 GB. The 5 GB budget allows about 83.3 MB/run if this is the only workload. | 5 GB |
| R2 Standard storage | At 30 MB/snapshot and 1 MB/delta, 6 snapshots and 46 deltas including the cleanup grace period occupy about 226 MB. | 10 GB-month |
| R2 requests | Roughly 300 Class A requests at three PUTs and two single-page LISTs per changed run. 1,000 clients polling six times/day and fetching one delta per poll use about 360,000 Class B requests before startup checks and retries. | 1 million A and 10 million B |

Use the Actions log for collection duration, source job count, unavailable query rate, changed jobs, package sizes, database bytes and retained R2 bytes. In Supabase **Organization → Usage**, compare the database size and metered egress over several runs, including a full sweep. In **R2 → bucket → Metrics**, compare storage and Class A/B operations with the estimate. A sustained average above the modeled retention or transfer budget needs a new capacity calculation. [Supabase allowances](https://supabase.com/docs/guides/platform/billing-on-supabase) · [R2 allowances](https://developers.cloudflare.com/r2/pricing/)

## Operator page

The operator page is a permanent page at `<feed domain>/ops/index.html`. It is
for operators only: it is unlisted and marked `noindex`. To restrict it,
protect the path `ops/*` with Cloudflare Access.
- The page (`scripts/cloud/ops_dashboard.html`) is deployed by the
  **Ops dashboard** workflow whenever it changes on `main`, or by hand with
  `workflow_dispatch`. It exists before the first collection run; until then
  it says that no runs have been recorded.
- Each collection run only adds data files.

| Object | Content |
|---|---|
| `ops/index.html` | The page itself (deployed by the Ops dashboard workflow) |
| `ops/history.json` | One compact row per run, newest first, 500 rows max: trends and the run list |
| `ops/runs/<run>-<attempt>.json` | Full report of every run, newest 500 kept |
| `ops/latest.json` | Copy of the last run's full report |
| `ops/logs/<run>-<attempt>.log` | The run's console log, gzip-encoded, newest 200 kept |

A run picker (also *Newer* / *Older*, or a click on a row of the history
table) selects which run the page details. The selection is kept in the URL
(`#run=...`), so a link opens the same run. Trend charts always show all runs,
with a dashed line at the selected run. Runs recorded before per-run reports
existed show only their history summary.

The page shows:
- **Totals for the selected run.** Live jobs in the database; jobs new, updated and
  closed this run; feed size; SimplifyJobs recall; duration; failure count.
  It links to the GitHub Actions run and to the run log, which you can
  download.
- **Trends over time.** Live jobs and feed size; new and closed jobs per run;
  jobs per collector; live jobs per opportunity type; SimplifyJobs recall; run
  duration.
- **Per-source tables.** Jobs per collector this run, with SimplifyJobs recall
  and the effect of gap filling. Per database source: live jobs, rows seen
  this run, rows new this run.
- **Breakdowns of live jobs.** By opportunity type (internship, co-op, new
  grad, ...), by title category, by top subcategories, by country, by
  recruiting term and by work mode. New jobs this run are also broken down by
  type.
- **Database size.** Total size and the largest tables, with a trend. The
  tile turns red at 400 MiB, ahead of the 500 MB Supabase free-tier limit.
- **Data quality.** Multi-source jobs, salary coverage, missing or short
  descriptions, missing posted dates, missing countries, category `other`,
  unknown type, and jobs closed but not yet deleted.
- **Health.** Indeed and LinkedIn query outcomes. ATS boards that were
  failed, missing, skipped at the deadline or truncated, and 429 cooldowns.
  Boards discovered through SimplifyJobs and Stapply. Stage timings, provider
  failures, and a sample of missed SimplifyJobs listings.

The same summary is appended to the GitHub Actions step summary.
`scripts/cloud/ops_stats.py` computes the database figures with read-only
aggregates after publishing; it treats `status` 1–2 as live.

**Logs.** The workflow's collection step pipes its output through `tee
run.log`. A final step runs `python -m scripts.cloud.ops_report upload-log
run.log` even when the run failed or was cancelled. Before upload, it replaces
the values of secret-looking environment variables and any `user:password@`
URL credentials. Monitoring never fails a run: report, statistics and log
uploads only print a warning on error.
