import { GlassButton } from "@zakisheriff/liquid-glass";
import { CampusConnection } from "./CampusControls";
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

function JobSearch({ waterlooWorks = false }: BoardProps) {
  return (
    <form id={waterlooWorks ? "ww-job-search-form" : "search-form"} className="posting-search" role="search"
      aria-label={waterlooWorks ? "Search WaterlooWorks" : "Search public jobs"}>
      <div id={waterlooWorks ? "ww-search-controls" : "posting-search-controls"} className="posting-search-controls">
        <PostingSearchControls waterlooWorks={waterlooWorks} />
      </div>
    </form>
  );
}

function SyncStatus({ waterlooWorks = false }: BoardProps) {
  const prefix = waterlooWorks ? "ww-" : "";
  return (
    <div className="results-meta">
      <GlassButton id={prefix + "back-to-top"} className="posting-to-top" size="sm" type="button"
        aria-controls={prefix + "job-list"} aria-label="Back to top of job list" title="Back to top of job list" hidden>
        <Icon name="chevron-right" /> Top
      </GlassButton>
      {!waterlooWorks && (
        <>
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
      <GlassButton id={prefix + "refresh"} className="posting-refresh" size="sm" variant="clear" type="button"
        title="Refresh jobs" aria-label="Refresh jobs" icon={<Icon name="refresh" />} />
    </div>
  );
}

export function JobWorkspace({ waterlooWorks = false }: BoardProps) {
  const prefix = waterlooWorks ? "ww-" : "";
  const detailId = waterlooWorks ? "ww-job-detail" : "public-job-detail";
  const resultsToolbar = (
    <div className="results-toolbar">
      <div>
        <h2 id={prefix + "results-title"}>
          Latest jobs
        </h2>
        <span
          id={waterlooWorks ? "ww-jobs-total" : "result-status"}
          className="result-tally"
          role="status"
        >
          Loading…
        </span>
        <GlassButton id={prefix + "reset-job-search"} className="discovery-reset" size="sm" variant="clear" type="button" hidden>
          Clear search
        </GlassButton>
      </div>
    </div>
  );
  return (
    <section className="posting-workspace discovery-workspace">
      <section className="results-panel" aria-label="Job index">
        <header className="posting-index-head">
          <div className="posting-view-heading">
            {!waterlooWorks && <h1>Public Jobs</h1>}
            {waterlooWorks
              ? <div id="ww-connection-control"><CampusConnection /></div>
              : <ViewSwitcher active="list" />}
          </div>
        </header>
        <div id={waterlooWorks ? "ww-jobs-scroll-content" : undefined} className="results-scroll-content">
          <JobSearch waterlooWorks={waterlooWorks} />
          {resultsToolbar}
          <div id={prefix + "job-list"} className="job-list" />
          <div id={prefix + "empty-state"} className="empty-state" hidden>
            <div className="empty-icon">
              <Icon name="search" />
            </div>
            <h3>No matching jobs</h3>
            <p>Try removing a filter or using a broader search term.</p>
            <GlassButton id={prefix + "jobs-empty-action"} size="sm" type="button" hidden>
              Clear filters
            </GlassButton>
          </div>
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
        aria-valuemin={26}
        aria-valuemax={55}
        aria-valuenow={34}
        aria-orientation="vertical"
        tabIndex={0}
      />
      <aside
        id={detailId + "-pane"}
        className="job-detail-pane"
        aria-label={waterlooWorks ? "Selected WaterlooWorks job" : "Selected public job"}
        aria-hidden="true"
      >
        <div className="job-detail-pane-toolbar">
          <GlassButton id={"close-" + detailId} className="job-detail-pane-close" size="sm" type="button"
            aria-label="Close job details">
            <Icon name="chevron-right" /> Show full list
          </GlassButton>
        </div>
        <div id={detailId} className="job-detail-content" />
      </aside>
    </section>
  );
}
