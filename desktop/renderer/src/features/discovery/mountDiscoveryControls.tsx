import { FilterControls } from "./JobFilters";
import { CampusConnection, CampusSyncActions } from "./CampusControls";
import { PostingSearchControls } from "./PostingSearch";
import { createRoot } from "react-dom/client";
import { PublicViewControl, WaterlooSyncChoice } from "./DiscoveryControls";

export function mountDiscoveryControls() {
  createRoot(document.getElementById("filter-controls")!).render(<FilterControls />);
  createRoot(document.getElementById("ww-filter-controls")!).render(<FilterControls waterlooWorks />);
  createRoot(document.getElementById("posting-search-controls")!).render(<PostingSearchControls />);
  createRoot(document.getElementById("ww-search-controls")!).render(<PostingSearchControls waterlooWorks />);
  createRoot(document.getElementById("ww-sync-actions")!).render(<CampusSyncActions />);
  createRoot(document.getElementById("ww-connection-control")!).render(<CampusConnection />);
  createRoot(document.getElementById("public-view-control")!).render(<PublicViewControl />);
  for (const option of ["jobs", "submitted"] as const) {
    createRoot(document.getElementById("ww-sync-choice-" + option)!).render(<WaterlooSyncChoice option={option} />);
  }
}
