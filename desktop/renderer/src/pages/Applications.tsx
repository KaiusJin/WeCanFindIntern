import { GlassButton, GlassCard, GlassInput } from "@zakisheriff/liquid-glass";
import { useLayoutEffect, useRef, useState } from "react";
import { ApplicationsSort } from "../features/applications/ApplicationsSort";
import { ApplicationActions, ApplicationChoice, ApplicationForm, ApplicationRecord } from "../features/applications/ApplicationRecord";
import { Icon } from "../layouts/AppChrome";

const stages = [
  ["", "All applications"],
  ["interested", "Interested"],
  ["applied", "Applied"],
  ["interview", "Interviews"],
  ["offer", "Offers"],
  ["rejected", "Rejected"],
] as const;

function PipelineIndex() {
  return (
    <aside className="pipeline-index" aria-label="Filter applications by stage">
      <div className="pipeline-stages">
        {stages.map(([key, label]) => (
          <GlassButton key={key} size="sm" className={`pipeline-stage stage-${key}${key ? "" : " active"}`}
            data-stat-stage={key} aria-pressed={!key} icon={key ? <i aria-hidden="true" /> : <Icon name="layers" />}>
            <span data-stage-label="">{label}</span><strong id={`stat-${key || "total"}`}>0</strong>
          </GlassButton>
        ))}
      </div>
    </aside>
  );
}

function RecordSearch() {
  const ref = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState("");
  useLayoutEffect(() => {
    const input = ref.current!;
    const read = () => setValue(input.value);
    input.addEventListener("application:fill", read);
    return () => input.removeEventListener("application:fill", read);
  }, []);
  return <GlassInput ref={ref} className="record-search" id="tracker-query" type="search"
    aria-label="Search applications" placeholder="Search records…" autoComplete="off"
    value={value} onChange={event => setValue(event.target.value)} />;
}

export function ApplicationsContents() {
  return (
      <div className="pipeline-workspace">
        <section className="pipeline-records" aria-labelledby="tracker-ledger-title">
          <header className="pipeline-head">
            <div className="pipeline-title">
              <h1 id="tracker-ledger-title">Applications</h1>
              <div className="pipeline-response">
                <strong id="stat-rate">0%</strong>
                <span>Response rate</span>
              </div>
            </div>
            <div className="pipeline-actions" id="application-actions"><ApplicationActions /></div>
          </header>
          <PipelineIndex />
          <div className="pipeline-content">
            <div className="applications-list-pane">
              <div className="tracker-control-panel">
                <RecordSearch />
                <div className="tracker-head-actions" id="applications-sort-controls">
                  <ApplicationsSort />
                </div>
              </div>

              <div id="tracker-list-view" className="tracker-view-pane">
                <div className="tracker-table-wrap">
                  <table className="tracker-table">
                    <thead>
                      <tr>
                        <th className="select-col">
                          <input id="tracker-select-page" type="checkbox" aria-label="Select page" />
                        </th>
                        <th>Role &amp; company</th>
                        <th>Stage</th>
                        <th>Location / source</th>
                        <th>Applied</th>
                        <th>Updated</th>
                      </tr>
                    </thead>
                    <tbody id="tracker-table-body"></tbody>
                  </table>
                  <div id="tracker-empty" className="empty-state" hidden>
                    <svg
                      className="tracker-empty-icon"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.7"
                      width="28"
                      height="28"
                      aria-hidden="true"
                    >
                      <use href="/ui/icons.svg#briefcase" />
                    </svg>
                    <h3>No applications found</h3>
                    <p>Try a broader search or add an application.</p>
                  </div>
                  <GlassCard id="tracker-bulk-bar" className="tracker-bulk-bar" padding="6px 8px 6px 16px" radius="999px" hidden>
                    <strong>
                      <span id="tracker-selected-count">0</span> selected
                    </strong>
                    <ApplicationChoice id="tracker-bulk-stage" label="Selected application stages"
                      choices={[{ value: "", label: "Move to…" }]} compact />
                    <GlassButton id="tracker-bulk-delete" size="sm" variant="tinted" destructive>Delete</GlassButton>
                    <GlassButton id="tracker-clear-selection" size="sm">Clear</GlassButton>
                  </GlassCard>
                </div>
                <div className="tracker-pagination">
                  <span id="tracker-page-summary" role="status"></span>
                  <div>
                    <GlassButton id="tracker-page-prev" size="sm">Previous</GlassButton>
                    <GlassButton id="tracker-page-next" size="sm">Next</GlassButton>
                  </div>
                  <div className="record-page-size">
                    Rows <ApplicationChoice id="tracker-page-size" label="Rows per page" initial="50" compact
                      choices={["25", "50", "100"].map(value => ({ value, label: value }))} />
                  </div>
                </div>
              </div>
            </div>
            <aside id="tracker-detail-drawer" className="tracker-detail-drawer applications-detail-pane"
              aria-hidden="true" aria-labelledby="drawer-title"><ApplicationRecord /></aside>
          </div>
        </section>
      </div>
  );
}

export function Applications() {
  return <div id="tab-tracker" className="tab-pane" hidden><ApplicationsContents /></div>;
}

export function ApplicationDialogs() {
  return (
    <div id="custom-job-dialog" role="dialog" popover="auto" data-workspace-popover="tab-tracker"
      className="workspace-form-popover application-form" aria-labelledby="custom-job-heading">
      <ApplicationForm />
    </div>
  );
}
