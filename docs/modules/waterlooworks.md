# WaterlooWorks Module

## Purpose and isolation

WaterlooWorks is a browser-assisted, local-only source. It is not inserted into the public PostgreSQL `jobs` table and is not passed through JobSpy’s cross-source deduplication. WaterlooWorks identity is the external Job ID; the same Job ID is stored once even if it appears on multiple boards.

The module consists of:

| File | Responsibility |
|---|---|
| `waterlooworks/browser.py` | Chrome discovery, launch, debug-port reuse, target selection, navigation, and JavaScript evaluation |
| `waterlooworks/extractor.py` | Board definitions and DOM/API posting extraction |
| `waterlooworks/collector.py` | Board-by-board asynchronous collection and progress updates |
| `waterlooworks/repository.py` | SQLite schema, upsert, board links, run history, and queries |
| `waterlooworks/state.py` | Snapshot and per-board state model |
| `waterlooworks/service.py` | Lifecycle orchestration and concurrency guard |
| `api/routes/waterlooworks.py` | HTTP boundary |

## Browser security boundary

The service starts a dedicated Chrome profile, normally at `~/.wecanfindintern/chrome-waterlooworks`, and connects through a local debugging endpoint. It can reuse an already-running matching debug session. The browser window is the only place where the user completes Waterloo SSO and MFA.

The service checks authentication by observing the WaterlooWorks account path and page readiness. It does not receive or store passwords, MFA codes, browser cookies, or session secrets in SQLite or PostgreSQL.

If Chrome is not in the detected system locations, `WATERLOOWORKS_CHROME_BINARY` supplies the executable. `WATERLOOWORKS_CHROME_PROFILE`, `WATERLOOWORKS_URL`, and `WATERLOOWORKS_DB_PATH` override the defaults.

## Board workflow

The configured boards are:

1. Co-op: Full-Cycle (`full_cycle`)
2. Employer-Student Direct (`employer_student_direct`)
3. Graduating jobs (`graduating`)
4. Contract jobs (`contract`)
5. Campus jobs (`campus`)

`WaterlooWorksCollector.collect_all()` processes them independently:

1. Open the board URL in the authenticated target.
2. Select/click `All Jobs` when required.
3. Wait for the result table or posting API to become available.
4. Extract the source Job IDs.
5. Read each posting detail.
6. Normalize the posting and upsert it by source Job ID.
7. Record discovered, successful, and failed counts for the board.
8. Continue to the next board even when this board fails.

The collector updates the shared `WaterlooWorksSnapshot`, so the UI can display progress without waiting for the whole run.

## Extraction contract

The normalized local record includes source Job ID, title, organization, division, location text, city/province/country, work mode, structured salary, posted date, application deadline, application URL, application delivery, required documents, source URL, description, and the raw posting payload.

Salary extraction converts WaterlooWorks fields into minimum, maximum, interval, and currency. Stored descriptions are cleaned of stray HTML before display. The raw payload remains local-only and is removed from list/detail API responses.

## SQLite persistence

`WaterlooWorksRepository` opens the configured SQLite file with foreign keys and a 30-second busy timeout. Its schema stores jobs, board memberships, collection runs, and per-board run rows. Writes are keyed by `source_job_id`.

On startup, the service reads the most recent run and reconstructs the UI snapshot. A normal re-encounter of an existing Job ID updates visibility/board information without treating the posting as a new public job and without comparing content against PostgreSQL jobs.

List queries support board, text query, limit, offset, and `include_description`. List responses always remove `raw_payload` and `payload_hash`; descriptions are omitted unless explicitly requested. Detail responses remove the same internal fields.

## Service states

The shared snapshot includes `idle`, `waiting_for_login`, `ready`, `collecting`, `importing`, `completed`, `partial`, and `failed`-style states as produced by the service/collector, together with browser state, page URL, run timestamps, run id, totals, and per-board counters.

- `launch`: opens/reuses Chrome and sets waiting-for-login.
- `get_status`: reconnects to the debug port, checks target/auth/page readiness, and reports whether the browser closed.
- `start_collection`: refuses to start without an authenticated WaterlooWorks target; refuses to start a second active collection; launches a task and returns immediately.
- `close`: cancels an active collector and closes the browser session.

If a board cannot be reached, the board is marked failed and the run can finish as partial. If the browser closes, the service reports idle/closed-browser state rather than claiming a successful sync.

## API

- `GET /api/v1/waterlooworks/status`
- `POST /api/v1/waterlooworks/launch`
- `POST /api/v1/waterlooworks/collect` (202)
- `GET /api/v1/waterlooworks/jobs?board=&query=&limit=&offset=&include_description=`
- `GET /api/v1/waterlooworks/jobs/{source_job_id}`

Invalid board values are rejected. Not-connected/not-authenticated collection requests return an error instead of opening an uncontrolled browser workflow.
