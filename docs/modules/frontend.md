# Frontend Module

## Architecture

The web UI is a static HTML/CSS/ES-module application served by FastAPI from `web/`. There is no build step in the repository. `index.html` provides the DOM and feature sections; `styles.css` provides the visual system; `modules/main.js` wires feature initialization and shared navigation.

The browser calls the versioned REST API directly with `fetch`. API route order is defined before the static mount so `/api/...` paths are not shadowed by the HTML fallback.

## Module map

| Module | Main responsibility |
|---|---|
| `main.js` | Application startup and global interactions |
| `navigation.js` | Tab activation and scroll behavior |
| `helpers.js` | HTML escaping, Markdown rendering, labels, dates, salary formatting, timeout fetch, drop zones |
| `jobs.js` | Public job search/list/detail and facets |
| `waterlooworks.js` | Local source status, collection, list, and detail |
| `tracker.js` | Application list, bookmarks, drawer, events, bulk operations, CSV, custom jobs |
| `profile.js` | Profile editor, resume upload/import draft, history, save/delete |
| `ats-shared.js` | Shared resume upload, ATS request, and score-breakdown rendering |
| `ats-score.js` | Standalone resume ATS parsing score plus grounded AI commentary |
| `ats-match.js` | Standalone resume-to-job match section |
| `cover-letter.js` | Profile/resume input, generation, review, DOCX/PDF export |
| `interview.js` | Questions, TTS, camera/recording, answer analysis |
| `agent.js` | Sessions, chat, tools, approval, memory, preferences |
| `settings.js` | Provider/model/key/Ollama settings and localStorage |

## Public job flow

`jobs.js` loads `/api/v1/jobs/facets`, maps structured facet codes into select options, reads search/filter controls, and builds query parameters. Any new filter resets the keyset cursor. `loadJobs({append})` shows loading/error state, requests a page, appends or replaces cards, saves `next_cursor`, and disables further loading when `has_more=false`.

Opening a card requests `/api/v1/jobs/{uuid}` and renders canonical title/company/location, salary, tags, description, source links, and Tracker action. Infinite scroll uses an observer; a back-to-top button appears after the configured scroll threshold.

## WaterlooWorks flow

`waterlooworks.js` polls `/status` while connecting/collecting, calls `/launch` or `/collect`, displays per-board progress and errors, and loads local postings with board/query controls. Details are fetched separately. A local job reference always remains distinguishable from a public UUID in Tracker and Agent actions.

## Tracker flow

The Tracker module keeps filters in the URL, fetches applications and both bookmark lists in parallel, and updates cards/buttons after mutations. The drawer loads the application, live/read-only JD content, source link actions, and event timeline. Bulk actions operate on selected application IDs; custom-job creation calls the full tracker create endpoint.

## Profile and generated-content flow

Profile forms are generated from section configuration. Repeated records can be added/removed in local state and then saved. Resume upload shows an import draft without overwriting saved data. Cover-letter and interview tools use the selected provider; ATS parsing readiness and job matching are deterministic and do not use provider settings.

## Agent flow

`agent.js` creates or restores sessions, loads messages and pending approvals, posts the current message with the selected provider/model/key, renders assistant text and tool results, and shows approval buttons for pending writes. Session rename, preferences, memory status, and memory deletion use their dedicated endpoints. The browser sends the open-job context as request context so the Agent can explain the currently viewed job without guessing its identity.

## Rendering and safety

`escapeHtml()` is used for ordinary dynamic text. Markdown rendering handles job descriptions and generated text through the shared helper rather than injecting raw provider output. Buttons and links are created from validated IDs/URLs where possible. API errors are converted into plain user-facing notices.

The API contract verifier scans front-end fetch/link references and checks that corresponding FastAPI routes exist. Node syntax checks run over every ES module. These checks catch path drift even though the UI has no bundler.

## Browser state

AI settings are stored locally because the service receives them per request. In the Electron desktop build, API keys are encrypted through the operating-system secure store and only non-secret preferences remain in renderer local storage; the browser development build retains its local-storage fallback. Tracker filter state is reflected in the URL for reload/share behavior. Server-backed application data—profile, jobs, resumes, Tracker, Agent conversations, approvals, memory, and preferences—comes from PostgreSQL/SQLite API calls rather than being treated as browser-only state.

## Loading, failure, and retry behavior

The UI distinguishes initial loading, empty success, partial/board progress, and
request failure. Filter changes reset keyset cursors; failed list/detail calls do
not advance the saved cursor. WaterlooWorks status is polled while a background
task is active, and the displayed board counters remain usable when one board
fails. Agent approval buttons are disabled/updated from the returned approval
state so a browser retry cannot assume that a write occurred.

The frontend does not implement an independent automatic retry loop for every
HTTP request. It displays the server's readable error and lets the user retry;
server-side collection, provider, and repository layers own bounded retries and
idempotency. In desktop mode, the preload bridge is the only path for secrets,
backup, tray, and background-collection commands.
