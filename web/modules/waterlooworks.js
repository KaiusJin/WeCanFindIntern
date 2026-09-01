import {
  $,
  escapeHtml,
  formatSalary,
  formatRelativeTime,
  renderJobCard,
  renderJobDetail,
  showErrorDialog,
  workModeLabel,
} from "./helpers.js";
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
import { setupInfiniteScroll } from "./pagination.js?v=20260831-jobboard-parity-v3";
import { syncDialogScrollLock } from "./settings.js";

let wwBusy = false;
let wwStatus = "idle";
let wwJobsRequestId = 0;
let wwJobsAbortController = null;
const wwJobsState = { cursor: null, hasMore: false, loading: false, total: 0 };

// =========================================================
// WATERLOOWORKS LOCAL IMPORT
// =========================================================

function renderWaterlooWorksStatus(data) {
  const previousStatus = wwStatus;
  const status = data.status || "idle";
  wwStatus = status;
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
    ? "Updating jobs…"
    : (data.last_job_update_at
      ? `Jobs updated ${formatRelativeTime(data.last_job_update_at)}`
      : "Jobs not updated yet");
  $("#ww-status-message").textContent = data.message || "";
  const boardSelect = $("#ww-job-board");
  if (boardSelect && !boardSelect.dataset.catalogLoaded && data.boards?.length) {
    const selected = boardSelect.value;
    boardSelect.innerHTML = [
      '<option value="">All boards</option>',
      ...data.boards.map((board) => `<option value="${escapeHtml(board.name)}">${escapeHtml(board.label || board.name)}</option>`),
      '<option value="applications">Submitted applications</option>',
    ].join("");
    boardSelect.value = selected;
    boardSelect.dataset.catalogLoaded = "true";
  }
  $("#ww-launch").disabled = ["browser_starting", "collecting", "importing", "syncing_applications"].includes(status);
  $("#ww-launch").textContent = data.browser_open ? "Open / check WaterlooWorks ↗" : "Log into WaterlooWorks ↗";
  $("#ww-collect").disabled = !data.browser_open || !["ready", "completed", "partial"].includes(status);
  $("#ww-collect").textContent = ["collecting", "importing"].includes(status)
    ? "Import in progress…"
    : "Import all job boards";
  $("#ww-sync-applications").disabled = !data.browser_open || !["ready", "completed", "partial"].includes(status);
  $("#ww-sync-applications").textContent = status === "syncing_applications"
    ? "Syncing applications…"
    : "Sync submitted applications";
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
  const board = $("#ww-job-board")?.value;
  if (query) params.set("query", query);
  if (board) params.set("board", board);
  if (append && wwJobsState.cursor) params.set("cursor", wwJobsState.cursor);
  const list = $("#ww-job-list");
  const loadingIndicator = $("#ww-loading-indicator");
  const endOfResults = $("#ww-end-of-results");
  wwJobsState.loading = true;
  if (!append) {
    wwJobsState.cursor = null;
    wwJobsState.hasMore = false;
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
    $("#ww-jobs-total").textContent = `${wwJobsState.total.toLocaleString()} jobs`;
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
    console.error("WaterlooWorks status error:", error);
  }
}

async function runWaterlooWorksAction(path) {
  try {
    const response = await fetch(`/api/v1/waterlooworks/${path}`, { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `WaterlooWorks action failed (${response.status}).`);
    renderWaterlooWorksStatus(data);
  } catch (error) {
    showErrorDialog(error, { title: "WaterlooWorks action failed" });
  }
}

$("#ww-launch")?.addEventListener("click", () => runWaterlooWorksAction("launch"));
$("#ww-collect")?.addEventListener("click", () => runWaterlooWorksAction("collect"));
$("#ww-sync-applications")?.addEventListener("click", () => runWaterlooWorksAction("applications/sync"));
$("#ww-job-search")?.addEventListener("click", loadWaterlooWorksJobs);
$("#ww-job-board")?.addEventListener("change", loadWaterlooWorksJobs);
$("#ww-job-query")?.addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadWaterlooWorksJobs();
});
setupInfiniteScroll({
  sentinelSelector: "#ww-infinite-scroll-sentinel",
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

async function openWaterlooWorksJob(sourceJobId) {
  const dialog = $("#job-dialog");
  const detail = $("#job-detail");
  detail.innerHTML = `<p class="loading-detail">Loading job details…</p>`;
  dialog.showModal();
  syncDialogScrollLock();
  try {
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
    setActiveJobContext(waterlooWorksJobContext({ ...job, description }));
    detail.innerHTML = renderJobDetail(job, {
      eyebrow: boardsLabel,
      company: job.organization,
      location: job.location_text,
      meta: `Job ID ${job.source_job_id}`,
      description,
      facts: [
        { label: "Salary", value: formatSalary(job.salary) },
        { label: "Work mode", value: workModeLabel(job.work_mode) },
        { label: "Application deadline", value: job.application_deadline || "Not specified", full: true },
      ],
      links: [job.application_url || job.source_url],
    });
  } catch (error) {
    if (dialog.open) dialog.close();
    syncDialogScrollLock();
    showErrorDialog(error, { title: "WaterlooWorks job details unavailable" });
  }
}
let wwPollTimer = null;
function startWaterlooWorksPolling() {
  if (wwPollTimer !== null) return;
  scheduleWaterlooWorksPoll();
}

function scheduleWaterlooWorksPoll() {
  const delay = wwBusy ? 2000 : 10000;
  wwPollTimer = setTimeout(() => {
    if ($("#tab-waterlooworks")?.classList.contains("active")) loadWaterlooWorksStatus();
    scheduleWaterlooWorksPoll();
  }, delay);
}

export {
  loadWaterlooWorksStatus,
  loadWaterlooWorksJobs,
  openWaterlooWorksJob,
  startWaterlooWorksPolling,
};
