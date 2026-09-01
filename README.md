# WeCanFindIntern

> Find the right opportunity, understand your fit, and move from discovery to
> application in one focused workspace.

[Visit the WeCanFindIntern product site](https://wcfi.kaiusjin.com/) for the
product introduction.

WeCanFindIntern is a local-first career workspace for internships, co-ops,
new-grad roles, and early-career opportunities. It brings job discovery,
candidate context, application preparation, interview practice, and progress
tracking into one workflow—so a job search becomes more than a stream of tabs
and links.

## One workspace for the whole search

```text
Discover → Understand → Prepare → Practice → Track → Improve
```

- **Discover:** compare opportunities from major public job sources and
  WaterlooWorks in a consistent experience.
- **Understand:** filter by normalized location, work mode, role type,
  schedule, skills, requirements, compensation, and recruiting term.
- **Prepare:** build a reusable candidate profile, review a resume import, run
  transparent ATS diagnostics, and create grounded application materials.
- **Practice:** generate role-specific interview questions, record or type
  answers, receive structured feedback, and follow progress across sessions.
- **Track:** keep bookmarks, applications, stages, deadlines, source updates,
  and event history together.
- **Improve:** use recommendations and a conversational assistant that works
  from your profile and activity while keeping changes under your control.

## Why it feels different

### Your evidence stays at the center

Profile, resume, ATS, cover-letter, interview, recommendation, and Agent
features share the same candidate context. Deterministic diagnostics expose the
signals behind their results, and generated writing is checked for unsupported
claims before it is presented for review.

### Job data becomes comparable

Indeed, LinkedIn, Glassdoor, ZipRecruiter, and Google Jobs records pass through
one stable data model. Source identity remains intact while locations, work
modes, opportunity types, schedules, skills, requirements, salary, and
recruiting terms become consistent enough to search and compare.

### WaterlooWorks remains private and isolated

WaterlooWorks uses a dedicated local Chrome profile for interactive Waterloo
SSO/MFA. Its postings and submitted-application status stay in a separate local
store while still participating in search, recommendations, bookmarks, and the
Tracker experience.

### AI assists; you decide

The Agent can search, explain, recommend, and prepare changes. Profile and
Tracker mutations always stop at a preview and require explicit approval. AI
features support Gemini, OpenAI, DeepSeek, GLM, Qwen, and local Ollama through a
shared provider boundary.

### Local-first by design

The desktop application bundles its application service and PostgreSQL runtime,
stores secrets through the operating system, keeps services on loopback, runs
scheduled collection locally, and manages local backups. The browser/developer
mode uses the same UI and API contracts.

## Product areas

| Area | What it provides |
|---|---|
| Jobs | Multi-source search, facets, map distribution, details, source links, and bookmarks |
| WaterlooWorks | Dedicated sign-in session, five-board import, application-status sync, and local search |
| Profile | Structured candidate record, secure PDF/LaTeX resume import, review draft, and export |
| ATS | Parsing-readiness and job-match diagnostics with evidence-backed commentary |
| Cover Letter | Writer/Reviewer generation loop with DOCX and PDF export |
| Interview | Question generation, local transcription, answer analysis, TTS, history, and trends |
| Tracker | Public, WaterlooWorks, and custom applications with stages, events, bulk actions, and CSV export |
| Agent | Search, recommendations, approval-gated changes, audit history, and layered memory |

## Ways to run

- **Desktop application:** self-contained macOS and Windows workflow with
  embedded services, secure storage, backups, tray operation, and background
  collection.
- **Browser/developer application:** FastAPI and the native ES-module frontend
  backed by a local PostgreSQL service.

Installation, operations, build, recovery, and verification commands live in
the technical documentation so this README can remain the product entry point.

## Documentation

- [Technical documentation](docs/TECHNICAL_DOCUMENTATION.md) — architecture,
  system boundaries, end-to-end flows, and implementation details.
- [Documentation index](docs/README.md) — module guides, runbooks, and contract
  references.
- [Operations and verification](docs/modules/operations.md) — development setup,
  collection, scheduling, maintenance, and acceptance checks.
- [Desktop application](docs/DESKTOP.md) — runtime, platform builds, data,
  security, backup, and recovery.
- [Reliability and recovery](docs/RELIABILITY_AND_RECOVERY.md) — failure
  outcomes, retries, idempotent reruns, concurrency, and operator actions.

## Responsible use

Collection and imported data can be governed by source-site terms, privacy
requirements, and applicable law. Operate the software with appropriate access,
rate limits, data handling, and retention policies.

## License

WeCanFindIntern is licensed under the [GNU Affero General Public License,
version 3 or later](LICENSE). Vendored and bundled third-party components retain
their own license terms. Job, resume, and profile data are not automatically
licensed by the AGPL.
