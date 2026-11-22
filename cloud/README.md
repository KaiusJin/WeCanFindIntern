# Cloud collector

A batch pipeline that GitHub Actions runs on a schedule (`python -m cloud.run`).
It is not part of the desktop app: `desktop/build/backend.spec` excludes it, and
`src/wecanfindintern` never imports it. It reuses `wecanfindintern.domain` as a
shared kernel.

```text
cloud/
├── run.py              Entry point: campaign → maintenance → publish → ops report
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
