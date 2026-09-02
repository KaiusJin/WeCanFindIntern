import {
  $,
  escapeHtml,
  formatSalary,
  formatRelativeTime,
  label,
  renderJobCard,
  renderJobDetail,
  showErrorDialog,
  workModeLabel,
} from "./helpers.js?v=20260901-error-dialog-minimal-v1";
import {
  BOOKMARK_FILLED,
  BOOKMARK_OUTLINE,
  bookmarkState,
  loadWaterlooWorksBookmarks,
  toggleWaterlooWorksBookmark,
  updateBookmarkButtons,
} from "./bookmarks.js";
import {
  setActiveJobContext,
  waterlooWorksJobContext,
} from "./job-context.js?v=20260831-jobboard-parity-v3";
import {
  closeJobDetailPane,
  openJobDetailPane,
} from "./job-detail-pane.js?v=20260902-shared-components-v1";
import {
  renderMultiFilter,
  selectedMultiFilterValues,
  setMultiFilterSheetOpen,
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
  const button = $("#ww-open-job-filters");
  if (button) button.textContent = total ? `Filters (${total})` : "Filters";
}

function setWaterlooWorksFilterSheetOpen(open) {
  setMultiFilterSheetOpen(
    "#ww-job-filters-sheet",
    "#ww-job-filters-backdrop",
    open,
    { closeMenus: true },
  );
}

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

function updateWaterlooWorksSyncSelection() {
  const syncJobs = Boolean($("#ww-sync-jobs")?.checked);
  const syncSubmitted = Boolean($("#ww-sync-submitted")?.checked);
  const selected = [syncJobs ? "Job postings" : "", syncSubmitted ? "submitted applications" : ""]
    .filter(Boolean);
  const hint = $("#ww-sync-selection-hint");
  const startButton = $("#ww-start-sync");
  $("#ww-sync-dialog .ww-board-list")?.classList.toggle("disabled", !syncJobs);
  if (hint) hint.textContent = selected.length ? `${selected.join(" and ")} selected` : "Select at least one item to sync";
  if (startButton) startButton.disabled = !selected.length || !wwBrowserOpen || !WW_READY_STATUSES.has(wwStatus);
}

function openWaterlooWorksSyncDialog() {
  const dialog = $("#ww-sync-dialog");
  if (!dialog || dialog.open || !wwBrowserOpen || !WW_READY_STATUSES.has(wwStatus)) return;
  updateWaterlooWorksSyncSelection();
  dialog.showModal();
}

function closeWaterlooWorksSyncDialog() {
  $("#ww-sync-dialog")?.close();
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
  const connectionStatus = $("#ww-connection-status");
  const actionBusy = ["browser_starting", "collecting", "importing", "syncing_applications"].includes(status);
  launchButton.disabled = actionBusy;
  if (actionBusy) {
    launchButton.textContent = status === "browser_starting" ? "Opening WaterlooWorks…" : "Syncing WaterlooWorks…";
  } else if (wwBrowserOpen && WW_READY_STATUSES.has(status)) {
    launchButton.textContent = "Sync WaterlooWorks";
  } else if (wwBrowserOpen) {
    launchButton.textContent = "Open / check WaterlooWorks ↗";
  } else {
    launchButton.textContent = "Log into WaterlooWorks ↗";
  }
  if (connectionStatus) {
    connectionStatus.classList.toggle("connected", wwBrowserOpen && WW_READY_STATUSES.has(status));
    connectionStatus.textContent = wwBrowserOpen && WW_READY_STATUSES.has(status)
      ? "Connected · Ready to sync"
      : (["waiting_for_login", "waiting_for_job_page"].includes(status)
        ? "Finish signing in in the WaterlooWorks window"
        : (actionBusy ? (data.message || "WaterlooWorks sync in progress") : "Not connected"));
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

function renderWaterlooWorksJobs(items, list, { append = false } = {}) {
  const markup = items.map((job) => {
    const boards = job.boards || [];
    const sourceBoards = boards.filter((value) => value !== "applications");
    const isSaved = bookmarkState.waterlooWorksJobs.has(job.source_job_id);
    return renderJobCard(job, {
      source: "waterlooworks",
      isSaved,
      primaryTag: sourceBoards.length
        ? (job.board_labels?.[sourceBoards[0]] || sourceBoards[0].replaceAll("_", " "))
        : "",
      secondaryTags: sourceBoards.slice(1).map(
        (value) => job.board_labels?.[value] || value.replaceAll("_", " "),
      ),
      boards,
      bookmarkIcon: isSaved ? BOOKMARK_FILLED : BOOKMARK_OUTLINE,
    });
  }).join("");
  if (append) list.insertAdjacentHTML("beforeend", markup);
  else list.innerHTML = markup;
  updateBookmarkButtons();
}

async function loadWaterlooWorksJobs({ append = false } = {}) {
  if (append && (wwJobsState.loading || !wwJobsState.hasMore)) return;
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
  if (append && wwJobsState.cursor) params.set("cursor", wwJobsState.cursor);
  const list = $("#ww-job-list");
  const loadingIndicator = $("#ww-loading-indicator");
  const endOfResults = $("#ww-end-of-results");
  wwJobsState.loading = true;
  if (!append) {
    wwJobsState.cursor = null;
    wwJobsState.hasMore = false;
    $("#ww-jobs-scroll-content")?.scrollTo({ top: 0, behavior: "instant" });
    list.innerHTML = '<p class="muted-copy">Loading WaterlooWorks jobs…</p>';
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
    $("#ww-jobs-total").textContent = `${formattedCount} result${wwJobsState.total === 1 ? "" : "s"}`;
    if (!items.length && !append) {
      list.innerHTML = '<p class="muted-copy">No matching WaterlooWorks jobs.</p>';
      return;
    }
    const existingIds = new Set(
      [...list.querySelectorAll(".ww-job-card")].map((card) => card.dataset.sourceJobId),
    );
    const newItems = items.filter((job) => !existingIds.has(job.source_job_id));
    renderWaterlooWorksJobs(newItems, list, { append });
  } catch (error) {
    if (requestId !== wwJobsRequestId) return;
    if (error.name === "AbortError") return;
    if (!append) {
      list.innerHTML = "";
      showErrorDialog(error, { title: "WaterlooWorks jobs unavailable" });
    } else {
      console.error("WaterlooWorks jobs could not load more results:", error);
    }
  } finally {
    if (controller.signal.aborted) return;
    wwJobsState.loading = false;
    if (loadingIndicator) loadingIndicator.hidden = true;
    if (endOfResults) endOfResults.hidden = wwJobsState.hasMore || !list.querySelector(".ww-job-card");
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
    const connectionStatus = $("#ww-connection-status");
    const launchButton = $("#ww-launch");
    connectionStatus?.classList.remove("connected");
    if (connectionStatus) connectionStatus.textContent = "Not connected";
    if (launchButton) {
      launchButton.disabled = false;
      launchButton.textContent = "Log into WaterlooWorks ↗";
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
    showErrorDialog(error, { title: "WaterlooWorks action failed" });
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
$("#ww-open-job-filters")?.addEventListener("click", () => setWaterlooWorksFilterSheetOpen(true));
$("#ww-close-job-filters")?.addEventListener("click", () => setWaterlooWorksFilterSheetOpen(false));
$("#ww-job-filters-backdrop")?.addEventListener("click", () => setWaterlooWorksFilterSheetOpen(false));
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
$("#ww-clear-filters")?.addEventListener("click", () => {
  $("#ww-job-filters-sheet")?.querySelectorAll('.multi-filter input[type="checkbox"]')
    .forEach((input) => { input.checked = false; });
  $("#ww-job-filters-sheet")?.querySelectorAll(".multi-filter")
    .forEach(updateMultiFilterSummary);
  WW_TEXT_FILTER_IDS.forEach((id) => {
    const input = $(`#${id}`);
    if (input) input.value = "";
  });
  if ($("#ww-filter-category")) $("#ww-filter-category").value = "";
  updateWaterlooWorksFilterCount();
  loadWaterlooWorksJobs();
});
$("#ww-sync-jobs")?.addEventListener("change", updateWaterlooWorksSyncSelection);
$("#ww-sync-submitted")?.addEventListener("change", updateWaterlooWorksSyncSelection);
$("#ww-close-sync-dialog")?.addEventListener("click", closeWaterlooWorksSyncDialog);
$("#ww-cancel-sync")?.addEventListener("click", closeWaterlooWorksSyncDialog);
$("#ww-sync-dialog")?.addEventListener("click", (event) => {
  if (event.target === event.currentTarget) closeWaterlooWorksSyncDialog();
});
$("#ww-start-sync")?.addEventListener("click", async () => {
  const syncJobs = Boolean($("#ww-sync-jobs")?.checked);
  const syncSubmitted = Boolean($("#ww-sync-submitted")?.checked);
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
    return;
  }
  const card = event.target.closest(".ww-job-card");
  if (card) {
    openWaterlooWorksJob(card.dataset.sourceJobId);
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") setWaterlooWorksFilterSheetOpen(false);
  if (!["Enter", " "].includes(event.key) || event.target.closest("button, a, input, select")) return;
  const card = event.target.closest(".ww-job-card");
  if (!card) return;
  event.preventDefault();
  openWaterlooWorksJob(card.dataset.sourceJobId);
});

async function loadWaterlooWorksJobDetail(sourceJobId) {
  const response = await fetch(`/api/v1/waterlooworks/jobs/${encodeURIComponent(sourceJobId)}`);
  if (!response.ok) throw new Error("Could not load job details");
  const job = await response.json();
  const description = job.description && ![
    "there was an error loading this job posting",
    "error loading this job posting",
  ].includes(job.description.trim().toLowerCase()) ? job.description : "";
  const boards = job.boards || [];
  const boardsLabel = boards.map(
    (value) => job.board_labels?.[value] || value.replaceAll("_", " "),
  ).join(" · ") || "All boards";
  const applicationDeadline = job.submitted_application_deadline || job.application_deadline;
  setActiveJobContext(waterlooWorksJobContext({ ...job, description }));
  return {
    job,
    html: renderJobDetail(job, {
      eyebrow: boardsLabel,
      company: job.organization,
      location: job.location_text,
      meta: `Job ID ${job.source_job_id}`,
      description,
      facts: [
        { label: "Salary", value: formatSalary(job.salary) },
        { label: "Work mode", value: workModeLabel(job.work_mode) },
        { label: "Application due", value: applicationDeadline || "Not specified", full: true },
      ],
      links: [job.application_url || job.source_url],
    }),
  };
}

async function openWaterlooWorksJob(sourceJobId) {
  await openJobDetailPane({
    paneSelector: "#ww-job-detail-pane",
    detailSelector: "#ww-job-detail",
    cardsSelector: "#ww-job-list .ww-job-card",
    selectedId: sourceJobId,
    getCardId: (card) => card.dataset.sourceJobId,
    loadDetail: loadWaterlooWorksJobDetail,
    errorTitle: "WaterlooWorks job details unavailable",
  });
}

function closeWaterlooWorksJobDetail() {
  closeJobDetailPane({
    paneSelector: "#ww-job-detail-pane",
    selectedCardsSelector: "#ww-job-list .ww-job-card.selected",
  });
}

$("#close-ww-job-detail")?.addEventListener("click", closeWaterlooWorksJobDetail);
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
  openWaterlooWorksJob,
  startWaterlooWorksPolling,
};
