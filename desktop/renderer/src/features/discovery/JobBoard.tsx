import { CampusSearchControls } from "./CampusControls";
import { PostingSearchControls } from "./PostingSearch";
import { PublicViewControl } from "./DiscoveryControls";
import { Icon } from "../../layouts/AppChrome";

export function ViewSwitcher({ active }: { active: "list" | "map" }) {
  return (
    <div data-public-jobs-view={active}>
      {active === "list" && <div id="public-view-control"><PublicViewControl /></div>}
    </div>
  );
}

type BoardProps = { waterlooWorks?: boolean };

export function JobSearch({ waterlooWorks = false }: BoardProps) {
  if (!waterlooWorks) {
    return (
      <form id="search-form" className="posting-search" role="search" aria-label="Search public jobs">
        <div id="posting-search-controls"><PostingSearchControls /></div>
      </form>
    );
  }
  return (
    <form
      id="ww-job-search-form"
      role="search"
      className="campus-search"
      aria-label="Search WaterlooWorks"
    >
      <div id="ww-search-controls"><CampusSearchControls /></div>
    </form>
  );
}

function SyncStatus({ waterlooWorks = false }: BoardProps) {
  const prefix = waterlooWorks ? "ww-" : "";
  return (
    <div className="results-meta">
      {!waterlooWorks && (
        <>
          <button
            id="back-to-top"
            className="posting-to-top glass"
            type="button"
            aria-controls="job-list"
            aria-label="Back to top of job list"
            title="Back to top of job list"
            hidden
          >
            <Icon name="chevron-right" /> Top
          </button>
          <span id="job-sync-status-text" className="job-sync-status-text" role="status" hidden />
          <span id="job-sync-status-separator" className="meta-dot desktop-only-meta" aria-hidden="true">
            ·
          </span>
        </>
      )}
      <span
        id={waterlooWorks ? "ww-jobs-updated-at" : "last-updated-text"}
        className="last-updated-text"
        title={waterlooWorks ? "Last WaterlooWorks job sync time" : "Last automated job sync time"}
      >
        {waterlooWorks ? "Not updated yet" : "Latest sync unavailable"}
      </span>
      <button
        id={prefix + "refresh"}
        className="icon-button control-glass"
        type="button"
        title="Refresh jobs"
        aria-label="Refresh jobs"
      >
        <Icon name="refresh" />
      </button>
    </div>
  );
}

export function JobWorkspace({ waterlooWorks = false }: BoardProps) {
  const prefix = waterlooWorks ? "ww-" : "";
  const detailId = waterlooWorks ? "ww-job-detail" : "public-job-detail";
  const resultsToolbar = (
    <div className="results-toolbar">
      <div>
        <h2 id={waterlooWorks ? undefined : "results-title"}>
          {waterlooWorks ? "WaterlooWorks jobs" : "Latest jobs"}
        </h2>
        <span
          id={waterlooWorks ? "ww-jobs-total" : "result-status"}
          className="result-tally"
          role="status"
        >
          Loading…
        </span>
        <button
          id={prefix + "reset-job-search"}
          className="text-button discovery-reset control-glass"
          type="button"
          hidden
        >
          Clear search
        </button>
      </div>
    </div>
  );
  return (
    <section
      className={
        waterlooWorks
          ? "ww-workspace discovery-workspace"
          : "workspace public-jobs-workspace discovery-workspace"
      }
    >
      <section
        id={waterlooWorks ? "ww-jobs-card" : undefined}
        className="results-panel"
        aria-label="Job index"
      >
        {!waterlooWorks && (
          <header className="posting-index-head">
            <div className="posting-view-heading">
              <h1>Public Jobs</h1>
              <ViewSwitcher active="list" />
            </div>
          </header>
        )}
        {waterlooWorks && resultsToolbar}
        {waterlooWorks && (
          <div className="discovery-column-head" aria-hidden="true">
            <span>Role &amp; company</span>
            <span>Location</span>
            <span>Compensation</span>
          </div>
        )}
        <div id={waterlooWorks ? "ww-jobs-scroll-content" : undefined} className="results-scroll-content">
          {!waterlooWorks && (
            <>
              <JobSearch />
              {resultsToolbar}
            </>
          )}
          <div id={prefix + "job-list"} className={waterlooWorks ? "job-list ww-job-list" : "job-list"}>
            {waterlooWorks && <p className="muted-copy">No WaterlooWorks jobs stored yet.</p>}
          </div>
          {!waterlooWorks && (
            <div id="empty-state" className="empty-state" hidden>
              <div className="empty-icon">
                <Icon name="search" />
              </div>
              <h3>No matching jobs</h3>
              <p>Try removing a filter or using a broader search term.</p>
              <button id="jobs-empty-action" className="secondary-button" type="button" hidden>
                Clear filters
              </button>
            </div>
          )}
          <div id={prefix + "infinite-scroll-sentinel"} className="infinite-scroll-sentinel">
            <div id={prefix + "loading-indicator"} className="loading-indicator" hidden>
              <div className="spinner" />
              <span>Loading more opportunities…</span>
            </div>
            <div id={prefix + "end-of-results"} className="end-of-results" hidden>
              <span>End of results</span>
            </div>
          </div>
        </div>
        <SyncStatus waterlooWorks={waterlooWorks} />
      </section>
      <div
        className="discovery-divider"
        role="separator"
        aria-label="Resize job list"
        aria-controls={detailId + "-pane"}
        aria-valuemin={waterlooWorks ? 35 : 26}
        aria-valuemax={waterlooWorks ? 64 : 55}
        aria-valuenow={waterlooWorks ? 45 : 34}
        aria-orientation="vertical"
        tabIndex={0}
      />
      <aside
        id={detailId + "-pane"}
        className={waterlooWorks ? "job-detail-pane ww-job-detail-pane" : "job-detail-pane"}
        aria-label={waterlooWorks ? "Selected WaterlooWorks job" : "Selected public job"}
        aria-hidden="true"
      >
        <div className="job-detail-pane-toolbar">
          <button
            id={"close-" + detailId}
            className="job-detail-pane-close control-glass"
            type="button"
            aria-label="Close job details"
          >
            <Icon name="chevron-right" /> Show full list
          </button>
        </div>
        <div id={detailId} className="job-detail-content" />
      </aside>
    </section>
  );
}
