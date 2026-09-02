# WeCanFindIntern

> Discover better opportunities, understand your fit, and move every application
> forward from one private career workspace.

[Explore the product](https://wcfi.kaiusjin.com/)

WeCanFindIntern brings internship, co-op, new-grad, and early-career job searching
into one focused workflow. It combines public job discovery, WaterlooWorks,
candidate context, application preparation, interview practice, and progress
tracking without turning a job search into a collection of disconnected tabs.

## A complete job-search workflow

```mermaid
flowchart LR
    D[Discover] --> C[Compare]
    C --> P[Prepare]
    P --> A[Apply]
    A --> I[Interview]
    I --> T[Track and improve]
```

- **Discover** roles from Indeed, LinkedIn, and five WaterlooWorks boards.
- **Compare** normalized locations, work modes, role types, schedules, skills,
  requirements, compensation, and recruiting terms.
- **Prepare** a reusable candidate profile, run transparent resume diagnostics,
  and create grounded application material.
- **Apply** with the original source link and keep every opportunity in a single
  application pipeline.
- **Interview** with role-specific questions, recorded or typed answers,
  structured feedback, and progress history.
- **Improve** through evidence-based recommendations and an assistant that can
  search, analyze, compare, and act on the workspace.

## Built around your evidence

The Profile is shared across recommendations, job analysis, ATS diagnostics,
cover letters, interview practice, and the AI Assistant. Deterministic scoring
shows the signals behind a result, while generated analysis separates confirmed
evidence, gaps, risks, and unknowns.

## Job data that is ready to compare

The collection pipeline turns source-specific records into one consistent job
library. It preserves source identity while normalizing the fields that matter
for decisions: location, work mode, opportunity type, schedule, category,
skills, requirements, salary, and recruiting term. Cross-source duplicates are
merged without losing the original application links.

## WaterlooWorks, kept local

WaterlooWorks runs through a dedicated Chrome profile so Waterloo SSO and MFA
stay in the browser. Job postings and submitted-application status live in a
separate local store, while still participating in search, recommendations,
bookmarks, job analysis, and the Application Tracker.

## AI that works inside clear boundaries

The AI Assistant can search jobs, analyze complete job descriptions, compare up
to five roles, recommend opportunities, prepare Profile changes, and work with
the Tracker. Routine Tracker additions and stage changes happen directly;
permanent Tracker removal and Profile replacement show an exact preview and wait
for approval. Gemini, OpenAI, DeepSeek, GLM, Qwen, and local Ollama share one
provider boundary.

## Product areas

| Area | Experience |
|---|---|
| Public Jobs | Multi-source search, rich filters, total results, infinite scrolling, map distribution, job details, and bookmarks |
| WaterlooWorks | Dedicated sign-in, five-board sync, local search, submitted-application sync, and Tracker handoff |
| Applications | Public, WaterlooWorks, and custom opportunities with stages, source snapshots, timelines, bulk actions, and CSV export |
| Profile | Structured candidate data, secure PDF/LaTeX resume import, editable review drafts, and reusable career context |
| Resume ATS Score | Deterministic parsing-readiness diagnostics with category evidence and optional grounded commentary |
| Job Match | Deterministic resume-to-job matching with requirements, gaps, eligibility evidence, and transparent scoring |
| Cover Letter | Writer/Reviewer generation loop with grounding checks and DOCX/PDF export |
| Interview Coach | Question generation, local transcription, TTS, structured answer analysis, history, and trends |
| AI Assistant | Job search, deep analysis, comparisons, recommendations, controlled actions, audit history, and layered memory |

## Local-first delivery

The desktop application packages the web experience, FastAPI service, and
PostgreSQL 16 runtime for macOS and Windows. Services bind to loopback, provider
keys use operating-system secure storage, backups are managed locally, and
scheduled collection continues from the tray. The browser/development runtime
uses the same UI and API contracts with a local PostgreSQL service.

## Documentation

- [Technical Documentation](docs/TECHNICAL_DOCUMENTATION.md) — complete runtime,
  architecture, data ownership, workflows, state transitions, and implementation
  contracts.
- [Documentation Index](docs/README.md) — module guides, reference contracts,
  operations, and recovery paths.
- [Operations and Verification](docs/modules/operations.md) — setup, collection,
  scheduling, maintenance, and acceptance commands.
- [Desktop Application](docs/DESKTOP.md) — packaging, local data, secure storage,
  backups, platform builds, and startup recovery.
- [Reliability and Recovery](docs/RELIABILITY_AND_RECOVERY.md) — retries,
  partial outcomes, idempotent reruns, concurrency, and operator actions.

## Responsible use

Job collection and imported data are governed by source terms, privacy
requirements, and applicable law. Use appropriate access, rate limits, data
handling, and retention policies.

## License

WeCanFindIntern is licensed under the [GNU Affero General Public License,
version 3 or later](LICENSE). Vendored and bundled third-party components retain
their own license terms. Job, resume, and Profile data are not automatically
licensed by the AGPL.
