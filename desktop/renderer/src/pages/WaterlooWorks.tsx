import { CampusSyncActions } from "../features/discovery/CampusControls";
import { JobFilters } from "../features/discovery/JobFilters";
import { Icon } from "../layouts/AppChrome";
import { WaterlooSyncChoice } from "../features/discovery/DiscoveryControls";
import { JobWorkspace } from "../features/discovery/JobBoard";

function SyncDialog() {
  return (
    <div
      role="dialog"
      popover="auto"
      data-workspace-popover="tab-waterlooworks"
      id="ww-sync-dialog"
      className="settings-dialog ww-sync-dialog workspace-form-popover"
      aria-labelledby="ww-sync-dialog-title"
    >
      <div className="settings-dialog-header">
        <div className="settings-title-wrap">
          <div className="settings-icon-badge" aria-hidden="true">
            <Icon name="campus" />
          </div>
          <div>
            <h3 id="ww-sync-dialog-title">Sync WaterlooWorks</h3>
            <p className="settings-subtitle">
              Choose what to bring into WeCanFindIntern.
            </p>
          </div>
        </div>
        <button
          id="ww-close-sync-dialog"
          popoverTarget="ww-sync-dialog" popoverTargetAction="hide"
          className="dialog-close control-glass"
          type="button"
          aria-label="Close"
        >
          ×
        </button>
      </div>

      <div className="settings-body ww-sync-dialog-body">
        <div className="ww-sync-choice">
          <div id="ww-sync-choice-jobs"><WaterlooSyncChoice option="jobs" /></div>
          <div className="ww-board-list" aria-label="Included boards">
            <p className="eyebrow">Included boards</p>
            {[
              "full_cycle",
              "employer_student_direct",
              "graduating",
              "contract",
              "campus",
            ].map((board) => (
              <div key={board} data-ww-board={board}>
                <strong>{board}</strong>
                <span className="ww-board-count">0 jobs</span>
                <em className="ww-board-state pending" aria-label="Pending">
                  …
                </em>
              </div>
            ))}
          </div>
        </div>

        <div className="ww-sync-choice">
          <div id="ww-sync-choice-submitted"><WaterlooSyncChoice option="submitted" /></div>
        </div>

        <section
          className="ww-run-summary"
          aria-labelledby="ww-run-summary-title"
        >
          <div className="ww-run-summary-heading">
            <p id="ww-run-summary-title" className="eyebrow">
              Run summary
            </p>
            <div className="ww-update-times" aria-label="Latest updates">
              <span id="ww-tracker-updated-at" className="muted-copy">
                Tracker not updated yet
              </span>
            </div>
          </div>
          <div className="ww-stat-grid">
            <div>
              <span>Stored jobs</span>
              <strong id="ww-unique-count">0</strong>
            </div>
            <div>
              <span>Latest import</span>
              <strong id="ww-posting-processed-count">0</strong>
            </div>
            <div>
              <span>Applications</span>
              <strong id="ww-application-count">0</strong>
            </div>
            <div>
              <span>Boards failed</span>
              <strong id="ww-board-failed-count">0</strong>
            </div>
          </div>
          <p
            id="ww-status-message"
            className="muted-copy"
            aria-live="polite"
          ></p>
        </section>

      </div>
      <footer className="settings-dialog-footer">
        <div id="ww-sync-actions" className="settings-footer-actions"><CampusSyncActions /></div>
      </footer>
    </div>
  );
}

export function WaterlooWorks() {
  return (
    <div id="tab-waterlooworks" className="tab-pane posting-page" hidden>
      <JobWorkspace waterlooWorks />
      <JobFilters waterlooWorks />
      <SyncDialog />
    </div>
  );
}
