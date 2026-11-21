import {
  $,
  formatRelativeTime,
  label,
  renderJobDetail,
  showToast,
  showFieldError,
  clearFieldError,
  workModeLabel,
} from "./helpers.js?v=20260901-error-dialog-minimal-v1";
import {
  bookmarkState,
  loadWaterlooWorksBookmarks,
  toggleWaterlooWorksBookmark,
} from "./bookmarks.js";
import {
  setActiveJobContext,
  waterlooWorksJobContext,
} from "./job-context.js?v=20260831-jobboard-parity-v3";
import { setupPostingWorkspace } from "./posting-workspace.js";
import {
  renderMultiFilter,
  selectedMultiFilterValues,
  setupFilterPopover,
  setupMultiFilterInteractions,
  updateMultiFilterSummary,
} from "./multi-filter.js?v=20260902-shared-filters-v1";
import { setupInfiniteScroll } from "./pagination.js?v=20260901-results-scroll-container-v1";
import { createDebouncedAction } from "./timing.js?v=20260902-shared-components-v1";

let wwBusy = false;
let wwStatus = "idle";
let wwBrowserOpen = false;
let wwOpenSyncDialogWhenReady = false;
let wwQueuedApplicationSync = false;
let wwJobsRequestId = 0;
let wwJobsAbortController = null;
const wwJobsState = { cursor: null, hasMore: false, loading: false, total: 0 };

const workspace = setupPostingWorkspace($("#tab-waterlooworks"), {
  waterlooWorks: true,
  loadDetail: loadWaterlooWorksJobDetail,
  activateJob: job => setActiveJobContext(waterlooWorksJobContext(job)),
});

const WW_READY_STATUSES = new Set(["ready", "completed", "partial"]);
const WW_TEXT_FILTER_IDS = [
  "ww-filter-skill",
  "ww-filter-company",
  "ww-filter-country",
  "ww-filter-region",
  "ww-filter-city",
  "ww-filter-posted-after",
];

function waterlooWorksFilterLabel(rootId, value) {
  if (rootId === "ww-work-mode-filter") return workModeLabel(value);
  if (rootId === "ww-opportunity-type-filter") return label(value);
  return value;
}

function setWaterlooWorksFilterOptions(rootId, items, placeholder) {
  renderMultiFilter(`#${rootId}`, items, {
    placeholder,
    getLabel: (item) => item.label || waterlooWorksFilterLabel(rootId, item.value),
    formatCount: (item) => Number.isFinite(Number(item.count))
      ? Number(item.count).toLocaleString()
      : "",
  });
}

function updateWaterlooWorksFilterCount() {
  const checkboxCount = document.querySelectorAll(
    "#ww-job-filters-sheet .multi-filter input[type=\"checkbox\"]:checked",
  ).length;
  const textCount = WW_TEXT_FILTER_IDS.filter((id) => $(`#${id}`)?.value.trim()).length;
  const categoryCount = $("#ww-filter-category")?.value ? 1 : 0;
  const total = checkboxCount + textCount + categoryCount;
  const button = $("#ww-filter-trigger-label");
  if (button) button.textContent = total ? `Filters (${total})` : "Filters";
}

setupFilterPopover($("#ww-job-filters-sheet"), $("#ww-open-job-filters"));

const debouncedLoadWaterlooWorksJobs = createDebouncedAction(() => loadWaterlooWorksJobs());

setWaterlooWorksFilterOptions("ww-board-source-filter", [], "All board sources");
setWaterlooWorksFilterOptions("ww-opportunity-type-filter", [
  { value: "internship" },
  { value: "co_op", label: "Co-op" },
  { value: "new_grad", label: "New grad" },
  { value: "apprenticeship" },
  { value: "regular" },
  { value: "contract" },
  { value: "temporary" },
  { value: "seasonal" },
], "All opportunity types");
setWaterlooWorksFilterOptions("ww-work-mode-filter", [
  { value: "remote" },
  { value: "hybrid" },
  { value: "onsite", label: "In-person" },
  { value: "unknown", label: "Work mode not specified" },
], "All work modes");

function waterlooSyncSelection() {
  return {
    syncJobs: $("#ww-sync-jobs").getAttribute("aria-checked") === "true",
    syncSubmitted: $("#ww-sync-submitted").getAttribute("aria-checked") === "true",
  };
}

function updateWaterlooWorksSyncSelection() {
  const { syncJobs, syncSubmitted } = waterlooSyncSelection();
  const selected = syncJobs || syncSubmitted;
  const submitted = $("#ww-sync-submitted");
  $("#ww-sync-dialog .ww-board-list")?.classList.toggle("disabled", !syncJobs);
  if (selected) clearFieldError(submitted);
  else showFieldError(submitted, "Select at least one item to sync.", { focus: false });
  $("#ww-start-sync").disabled = !selected || !wwBrowserOpen || !WW_READY_STATUSES.has(wwStatus);
}

function openWaterlooWorksSyncDialog() {
  const dialog = $("#ww-sync-dialog");
  if (!dialog || dialog.matches(":popover-open") || !wwBrowserOpen || !WW_READY_STATUSES.has(wwStatus)) return;
  updateWaterlooWorksSyncSelection();
  dialog.showPopover({ source: $("#ww-launch") });
}

function closeWaterlooWorksSyncDialog() {
  $("#ww-sync-dialog")?.hidePopover();
}

// =========================================================
// WATERLOOWORKS LOCAL IMPORT
// =========================================================

function renderWaterlooWorksStatus(data) {
  const previousStatus = wwStatus;
  const status = data.status || "idle";
  wwStatus = status;
  wwBrowserOpen = Boolean(data.browser_open);
  wwBusy = [
    "browser_starting",
    "waiting_for_login",
    "waiting_for_job_page",
    "collecting",
    "importing",
    "syncing_applications",
  ].includes(status);
  $("#ww-unique-count").textContent = Number(data.unique_job_count || 0).toLocaleString();
  const processedCount = Number(data.posting_inserted_count || 0) + Number(data.posting_known_count || 0);
  $("#ww-posting-processed-count").textContent = processedCount.toLocaleString();
  $("#ww-application-count").textContent = Number(data.application_count || 0).toLocaleString();
  $("#ww-board-failed-count").textContent = Number(data.board_failed_count || 0).toLocaleString();
  const trackerUpdated = $("#ww-tracker-updated-at");
  const jobsUpdated = $("#ww-jobs-updated-at");
  trackerUpdated.textContent = status === "syncing_applications"
    ? "Updating Tracker…"
    : (data.last_tracker_update_at
      ? `Tracker updated ${formatRelativeTime(data.last_tracker_update_at)}`
      : "Tracker not updated yet");
  jobsUpdated.textContent = ["collecting", "importing"].includes(status)
    ? "Updating…"
    : (data.last_job_update_at
      ? `Updated ${formatRelativeTime(data.last_job_update_at)}`
      : "Not updated yet");
  jobsUpdated.title = data.last_job_update_at
    ? `Last WaterlooWorks job sync: ${new Date(data.last_job_update_at).toLocaleString()}`
    : "Last WaterlooWorks job sync time";
  $("#ww-status-message").textContent = data.message || "";
  setWaterlooWorksFilterOptions("ww-board-source-filter", [
    ...(data.boards || []).map((board) => ({
      value: board.name,
      label: board.label || board.name,
      count: Number(board.posting_inserted_count || 0) + Number(board.posting_known_count || 0),
    })),
    { value: "applications", label: "Submitted applications", count: Number(data.application_count || 0) },
  ], "All board sources");
  updateWaterlooWorksFilterCount();
  const launchButton = $("#ww-launch");
  const actionBusy = ["browser_starting", "collecting", "importing", "syncing_applications"].includes(status);
  launchButton.disabled = actionBusy;
  if (actionBusy) {
    $("#ww-launch-label").textContent = status === "browser_starting" ? "Opening WaterlooWorks…" : "Syncing WaterlooWorks…";
  } else if (wwBrowserOpen && WW_READY_STATUSES.has(status)) {
    $("#ww-launch-label").textContent = "Sync WaterlooWorks";
  } else if (wwBrowserOpen) {
    $("#ww-launch-label").textContent = "Open / check WaterlooWorks ↗";
  } else {
    $("#ww-launch-label").textContent = "Log into WaterlooWorks ↗";
  }
  for (const board of data.boards || []) {
    const row = document.querySelector(`[data-ww-board="${board.name}"]`);
    if (!row) continue;
    const countLabel = row.querySelector(".ww-board-count");
    const stateLabel = row.querySelector(".ww-board-state");
    row.querySelector("strong").textContent = board.label || board.name;
    const labels = {
      pending: "Pending",
      collecting: "Collecting…",
      completed: "Completed",
      failed: "Failed",
    };
    const marks = { pending: "…", collecting: "…", completed: "✓", failed: "✗" };
    const boardSeen = Number(board.posting_inserted_count || 0) + Number(board.posting_known_count || 0);
    countLabel.textContent = `${boardSeen.toLocaleString()} jobs`;
    stateLabel.className = `ww-board-state ${board.status}`;
    stateLabel.textContent = marks[board.status] || "…";
    stateLabel.setAttribute("aria-label", labels[board.status] || board.status);
  }
  if (previousStatus === "syncing_applications" && ["completed", "partial"].includes(status)) {
    loadWaterlooWorksJobs();
    document.dispatchEvent(new CustomEvent("tracker:data-invalidated"));
  }
  if (["collecting", "importing"].includes(previousStatus) && ["completed", "partial"].includes(status)) {
    loadWaterlooWorksJobs();
    if (wwQueuedApplicationSync) {
      wwQueuedApplicationSync = false;
      void runWaterlooWorksAction("applications/sync");
    }
  } else if (["collecting", "importing"].includes(previousStatus) && status === "failed") {
    wwQueuedApplicationSync = false;
  }
  updateWaterlooWorksSyncSelection();
  if (wwOpenSyncDialogWhenReady && wwBrowserOpen && WW_READY_STATUSES.has(status)) {
    wwOpenSyncDialogWhenReady = false;
    queueMicrotask(openWaterlooWorksSyncDialog);
  }
}

async function loadWaterlooWorksJobs({ append = false } = {}) {
  if (append && (wwJobsState.loading || !wwJobsState.hasMore)) return;
  const posted = $("#ww-filter-posted-after");
  if (posted.validity.badInput) {
    showFieldError(posted, "Enter a complete posting date.", { focus: false });
    return;
  }
  clearFieldError(posted);
  if (!append) {
    wwJobsRequestId += 1;
    wwJobsAbortController?.abort();
  }
  const requestId = wwJobsRequestId;
  const params = new URLSearchParams({ limit: "20" });
  const query = $("#ww-job-query")?.value.trim();
  const location = $("#ww-job-location")?.value.trim();
  if (query) params.set("query", query);
  if (location) params.set("location", location);
  selectedMultiFilterValues("#ww-board-source-filter")
    .forEach((value) => params.append("board", value));
  selectedMultiFilterValues("#ww-work-mode-filter")
    .forEach((value) => params.append("work_mode", value));
  selectedMultiFilterValues("#ww-opportunity-type-filter")
    .forEach((value) => params.append("opportunity_type", value));
  const scalarFilters = {
    company: $("#ww-filter-company")?.value.trim(),
    skill: $("#ww-filter-skill")?.value.trim(),
    category: $("#ww-filter-category")?.value,
    country: $("#ww-filter-country")?.value.trim(),
    region: $("#ww-filter-region")?.value.trim(),
    city: $("#ww-filter-city")?.value.trim(),
    posted_after: $("#ww-filter-posted-after")?.value,
  };
  Object.entries(scalarFilters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  const filtered = params.size > 1;
  $("#ww-results-title").textContent = filtered ? "Search results" : "Latest jobs";
  $("#ww-reset-job-search").hidden = !filtered;
  if (append && wwJobsState.cursor) params.set("cursor", wwJobsState.cursor);
  const list = $("#ww-job-list");
  const loadingIndicator = $("#ww-loading-indicator");
  const endOfResults = $("#ww-end-of-results");
  wwJobsState.loading = true;
  if (!append) {
    wwJobsState.cursor = null;
    wwJobsState.hasMore = false;
    $("#ww-jobs-scroll-content")?.scrollTo({ top: 0, behavior: "instant" });
    list.replaceChildren();
    $("#ww-empty-state").hidden = true;
    $("#ww-jobs-total").textContent = "Searching…";
    if (endOfResults) endOfResults.hidden = true;
  } else if (loadingIndicator) {
    loadingIndicator.hidden = false;
  }
  const controller = new AbortController();
  wwJobsAbortController = controller;
  try {
    const bookmarksPromise = append
      ? Promise.resolve()
      : loadWaterlooWorksBookmarks().catch((error) => {
        console.warn("WaterlooWorks bookmarks unavailable:", error);
      });
    const response = await fetch(`/api/v1/waterlooworks/jobs?${params}`, { signal: controller.signal });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not load WaterlooWorks jobs.");
    await bookmarksPromise;
    if (requestId !== wwJobsRequestId) return;
    const items = data.items || [];
    wwJobsState.total = Number(data.total_count || 0);
    wwJobsState.cursor = data.next_cursor || null;
    wwJobsState.hasMore = Boolean(data.has_more && wwJobsState.cursor);
    const formattedCount = wwJobsState.total.toLocaleString("en-US");
    const countLabel = `${formattedCount} result${wwJobsState.total === 1 ? "" : "s"}`;
    $("#ww-jobs-total").textContent = countLabel;
    $("#ww-facet-results-label").textContent = "Show " + countLabel;
    workspace.renderRows(items, append);
    if (!items.length && !append) {
      if (filtered) {
        workspace.showEmpty("No matching jobs", "Try a broader search term or remove a filter.", "Clear search", () => $("#ww-reset-job-search").click());
      } else {
        workspace.showEmpty("No imported postings yet", "Connect to WaterlooWorks and sync a job board to browse its postings here.");
      }
    }
  } catch (error) {
    if (requestId !== wwJobsRequestId) return;
    if (error.name === "AbortError") return;
    if (!append) {
      workspace.closeDetail(false);
      workspace.showEmpty("Job feed unavailable", "Try reloading the WaterlooWorks postings.", "Retry", () => loadWaterlooWorksJobs());
      $("#ww-jobs-total").textContent = "Unavailable";
      $("#ww-facet-results-label").textContent = "Show results";
    } else {
      showToast("Could not load more WaterlooWorks jobs. Try refreshing the list.", { variant: "error" });
    }
  } finally {
    if (controller.signal.aborted) return;
    wwJobsState.loading = false;
    if (loadingIndicator) loadingIndicator.hidden = true;
    if (endOfResults) endOfResults.hidden = wwJobsState.hasMore || !list.children.length;
  }
}

async function loadWaterlooWorksStatus() {
  try {
    const response = await fetch("/api/v1/waterlooworks/status");
    if (!response.ok) throw new Error("Could not read WaterlooWorks status.");
    renderWaterlooWorksStatus(await response.json());
  } catch (error) {
    wwBrowserOpen = false;
    wwStatus = "idle";
    wwBusy = false;
    const launchButton = $("#ww-launch");
    if (launchButton) {
      launchButton.disabled = false;
      $("#ww-launch-label").textContent = "Log into WaterlooWorks ↗";
    }
    closeWaterlooWorksSyncDialog();
    console.error("WaterlooWorks status error:", error);
  }
}

async function runWaterlooWorksAction(path) {
  try {
    const response = await fetch(`/api/v1/waterlooworks/${path}`, { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `WaterlooWorks action failed (${response.status}).`);
    renderWaterlooWorksStatus(data);
    return data;
  } catch (error) {
    showToast(error.message || "Could not complete the WaterlooWorks action.", { variant: "error" });
    return null;
  }
}

$("#ww-launch")?.addEventListener("click", async () => {
  if (wwBrowserOpen && WW_READY_STATUSES.has(wwStatus)) {
    openWaterlooWorksSyncDialog();
    return;
  }
  wwOpenSyncDialogWhenReady = true;
  const result = await runWaterlooWorksAction("launch");
  if (!result) wwOpenSyncDialogWhenReady = false;
});
$("#ww-job-search-form")?.addEventListener("submit", (event) => {
  event.preventDefault();
  loadWaterlooWorksJobs();
});
$("#ww-refresh")?.addEventListener("click", () => {
  loadWaterlooWorksStatus();
  loadWaterlooWorksJobs();
});
$("#ww-reset-job-search")?.addEventListener("click", () => {
  $("#ww-job-search-form").reset();
  $("#ww-clear-filters").click();
});
setupMultiFilterInteractions("#ww-job-filters-sheet", {
  scope: $("#ww-job-filters-sheet"),
  onClear: () => {
    updateWaterlooWorksFilterCount();
    debouncedLoadWaterlooWorksJobs(100);
  },
  onChange: () => {
    updateWaterlooWorksFilterCount();
    debouncedLoadWaterlooWorksJobs(150);
  },
});
$("#ww-job-filters-sheet")?.addEventListener("input", (event) => {
  if (!event.target.matches("input[type=text], input[type=date]")) return;
  updateWaterlooWorksFilterCount();
  debouncedLoadWaterlooWorksJobs(300);
});
$("#ww-filter-category").addEventListener("change", () => {
  updateWaterlooWorksFilterCount();
  debouncedLoadWaterlooWorksJobs(150);
});
$("#ww-show-filter-results").addEventListener("click", () => {
  if ($("#ww-filter-posted-after").validity.badInput) {
    $("#ww-job-filters-sheet").dispatchEvent(new Event("wcfi:show-final-filter-group"));
    showFieldError($("#ww-filter-posted-after"), "Enter a complete posting date.");
    return;
  }
  loadWaterlooWorksJobs();
  $("#ww-job-filters-sheet").hidePopover();
});
$("#ww-clear-filters")?.addEventListener("click", () => {
  $("#ww-job-filters-sheet")?.querySelectorAll('.multi-filter input[type="checkbox"]')
    .forEach((input) => { input.checked = false; });
  $("#ww-job-filters-sheet")?.querySelectorAll(".multi-filter")
    .forEach(updateMultiFilterSummary);
  updateWaterlooWorksFilterCount();
  loadWaterlooWorksJobs();
});
$("#ww-sync-dialog").addEventListener("change", updateWaterlooWorksSyncSelection);
$("#ww-start-sync")?.addEventListener("click", async () => {
  const { syncJobs, syncSubmitted } = waterlooSyncSelection();
  if (!syncJobs && !syncSubmitted) return;
  closeWaterlooWorksSyncDialog();
  if (syncJobs) {
    wwQueuedApplicationSync = syncSubmitted;
    const result = await runWaterlooWorksAction("collect");
    if (!result) {
      wwQueuedApplicationSync = false;
      return;
    }
    if (syncSubmitted && ["completed", "partial"].includes(result.status)) {
      wwQueuedApplicationSync = false;
      await runWaterlooWorksAction("applications/sync");
    }
  } else {
    await runWaterlooWorksAction("applications/sync");
  }
});
setupInfiniteScroll({
  sentinelSelector: "#ww-infinite-scroll-sentinel",
  rootSelector: "#ww-jobs-scroll-content",
  isLoading: () => wwJobsState.loading,
  canLoadMore: () => wwJobsState.hasMore && wwJobsState.cursor,
  loadMore: () => loadWaterlooWorksJobs({ append: true }),
});
document.addEventListener("click", (event) => {
  const bookmarkBtn = event.target.closest(".ww-bookmark-btn");
  if (bookmarkBtn) {
    event.stopPropagation();
    toggleWaterlooWorksBookmark(bookmarkBtn.dataset.sourceJobId);
  }
});

async function loadWaterlooWorksJobDetail(sourceJobId, signal) {
  const response = await fetch(`/api/v1/waterlooworks/jobs/${encodeURIComponent(sourceJobId)}`, { signal });
  if (!response.ok) throw new Error("Could not load job details");
  const job = await response.json();
  if (!signal) setActiveJobContext(waterlooWorksJobContext(job));
  return {
    job,
    html: renderJobDetail(job, {
      waterlooWorks: true,
      isSaved: bookmarkState.waterlooWorksJobs.has(job.source_job_id),
    }),
  };
}

let wwPollTimer = null;
function startWaterlooWorksPolling() {
  if (wwPollTimer !== null) return;
  scheduleWaterlooWorksPoll();
}

function scheduleWaterlooWorksPoll() {
  const delay = wwBusy || wwBrowserOpen ? 2000 : 10000;
  wwPollTimer = setTimeout(() => {
    if ($("#tab-waterlooworks")?.classList.contains("active")) loadWaterlooWorksStatus();
    scheduleWaterlooWorksPoll();
  }, delay);
}

export {
  loadWaterlooWorksStatus,
  loadWaterlooWorksJobs,
  loadWaterlooWorksJobDetail,
  startWaterlooWorksPolling,
};
