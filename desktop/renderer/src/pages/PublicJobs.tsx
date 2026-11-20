import { JobWorkspace } from "../features/discovery/JobBoard";
import { JobFilters } from "../features/discovery/JobFilters";

export function PublicJobs() {
  return (
    <div id="tab-jobs" className="tab-pane active">
      <JobWorkspace />
      <JobFilters />
    </div>
  );
}
