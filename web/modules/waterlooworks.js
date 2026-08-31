import {
  $,
  escapeHtml,
  formatDate,
  formatSalary,
  formatRelativeTime,
  renderMarkdown,
  showErrorDialog,
  workModeLabel,
} from "./helpers.js";
import { syncDialogScrollLock } from "./settings.js";
import {
  loadWaterlooWorksBookmarks,
  toggleWaterlooWorksBookmark,
  trackerState,
  updateBookmarkButtons,
} from "./tracker.js";

let wwBusy = false;

// =========================================================
// WATERLOOWORKS LOCAL IMPORT
// =========================================================

const WW_BOARD_LABELS = {
  full_cycle: "Co-op: Full-Cycle",
  employer_student_direct: "Employer-Student Direct",
  graduating: "Graduating",
  contract: "Contract",
  campus: "Campus",
};

const WW_BOOKMARK_OUTLINE = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>`;
const WW_BOOKMARK_FILLED = `<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>`;

function renderWaterlooWorksStatus(data) {
  const status = data.status || "idle";
  wwBusy = [
    "browser_starting",
    "waiting_for_login",
    "waiting_for_job_page",
    "collecting",
    "importing",
  ].includes(status);
  $("#ww-unique-count").textContent = Number(data.unique_job_count || 0).toLocaleString();
  $("#ww-posting-success-count").textContent = Number(data.posting_success_count || 0).toLocaleString();
  $("#ww-board-failed-count").textContent = Number(data.board_failed_count || 0).toLocaleString();
  $("#ww-finished-at").textContent = data.finished_at
    ? `Finished ${formatRelativeTime(data.finished_at)}`
    : (data.started_at ? `Started ${formatRelativeTime(data.started_at)}` : "No import yet");
  $("#ww-launch").disabled = ["browser_starting", "collecting", "importing"].includes(status);
  $("#ww-launch").textContent = data.browser_open ? "Open / check WaterlooWorks ↗" : "Log into WaterlooWorks ↗";
  $("#ww-collect").disabled = !data.browser_open || !["ready", "completed", "partial"].includes(status);
  $("#ww-collect").textContent = ["collecting", "importing"].includes(status)
    ? "Import in progress…"
    : "Import all job boards";
  for (const board of data.boards || []) {
    const row = document.querySelector(`[data-ww-board="${board.name}"]`);
    if (!row) continue;
    const countLabel = row.querySelector(".ww-board-count");
    const stateLabel = row.querySelector(".ww-board-state");
    const labels = {
      pending: "Pending",
      collecting: "Collecting…",
      completed: "Completed",
      failed: "Failed",
    };
    const marks = { pending: "…", collecting: "…", completed: "✓", failed: "✗" };
    countLabel.textContent = `${Number(board.posting_success_count || 0).toLocaleString()} jobs`;
    stateLabel.className = `ww-board-state ${board.status}`;
    stateLabel.textContent = marks[board.status] || "…";
    stateLabel.setAttribute("aria-label", labels[board.status] || board.status);
  }
}

async function loadWaterlooWorksJobs() {
  const params = new URLSearchParams({ limit: "100" });
  const query = $("#ww-job-query")?.value.trim();
  const board = $("#ww-job-board")?.value;
  if (query) params.set("query", query);
  if (board) params.set("board", board);
  const list = $("#ww-job-list");
  try {
    await loadWaterlooWorksBookmarks();
    const response = await fetch(`/api/v1/waterlooworks/jobs?${params}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not load WaterlooWorks jobs.");
    $("#ww-jobs-total").textContent = `${Number(data.total || 0).toLocaleString()} jobs`;
    if (!data.items?.length) {
      list.innerHTML = '<p class="muted-copy">No matching WaterlooWorks jobs.</p>';
      return;
    }
    list.innerHTML = data.items.map((job) => {
      const boards = job.boards || [];
      const isSaved = trackerState.waterlooWorksTracked?.has(job.source_job_id);
      return `
        <article class="job-card ww-job-card" data-source-job-id="${escapeHtml(job.source_job_id)}" data-boards="${escapeHtml(boards.join(","))}">
          <div class="job-card-main">
            <div class="company-mark">${escapeHtml((job.organization || "?").slice(0, 1).toUpperCase())}</div>
            <div class="job-copy">
              <h3>${escapeHtml(job.title)}</h3>
              <p class="company-name">${escapeHtml(job.organization || "Company not specified")}</p>
              <p class="job-location">${escapeHtml(job.location_text || "Location not specified")} <span>·</span> ${escapeHtml(formatDate(job.date_posted))}</p>
            </div>
            <div style="display:flex; flex-direction:column; align-items:flex-end; gap:6px;">
              <button type="button" class="ww-bookmark-btn ${isSaved ? "saved" : ""}" data-source-job-id="${escapeHtml(job.source_job_id)}" title="${isSaved ? "Tracked in Pipeline" : "Bookmark / Track Job"}" aria-pressed="${isSaved}">${isSaved ? WW_BOOKMARK_FILLED : WW_BOOKMARK_OUTLINE}</button>
              <div class="job-date">Job ID ${escapeHtml(job.source_job_id)}</div>
            </div>
          </div>
          <div class="job-card-footer">
            <div class="job-tags">
              <span class="tag accent">WaterlooWorks</span>
              ${boards.map((value) => `<span class="tag">${escapeHtml(WW_BOARD_LABELS[value] || value.replaceAll("_", " "))}</span>`).join("")}
            </div>
            <span class="salary">${escapeHtml(formatSalary(job.salary))}</span>
          </div>
        </article>
      `;
    }).join("");
    updateBookmarkButtons();
  } catch (error) {
    list.innerHTML = "";
    showErrorDialog(error, { title: "WaterlooWorks jobs unavailable" });
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
$("#ww-job-search")?.addEventListener("click", loadWaterlooWorksJobs);
$("#ww-job-board")?.addEventListener("change", loadWaterlooWorksJobs);
$("#ww-job-query")?.addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadWaterlooWorksJobs();
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
    openWaterlooWorksJob(card.dataset.sourceJobId, card.dataset.boards);
  }
});

async function openWaterlooWorksJob(sourceJobId, boardsCsv) {
  const dialog = $("#job-dialog");
  const detail = $("#job-detail");
  detail.innerHTML = `<p class="loading-detail">Loading job details…</p>`;
  dialog.showModal();
  syncDialogScrollLock();
  try {
    const response = await fetch(`/api/v1/waterlooworks/jobs/${encodeURIComponent(sourceJobId)}`);
    if (!response.ok) throw new Error("Could not load job details");
    const job = await response.json();
    const boards = (boardsCsv || "").split(",").filter(Boolean);
    const boardsLabel = boards.map((value) => WW_BOARD_LABELS[value] || value.replaceAll("_", " ")).join(" · ") || "All boards";
    detail.innerHTML = `
      <p class="eyebrow">WaterlooWorks · ${escapeHtml(boardsLabel)}</p>
      <h2>${escapeHtml(job.title)}</h2>
      <p class="detail-company">${escapeHtml(job.organization || "Company not specified")}</p>
      <p class="detail-location">${escapeHtml(job.location_text || "Location not specified")} · Job ID ${escapeHtml(job.source_job_id)}</p>
      <div class="detail-grid">
        <div><span>Salary</span><strong>${escapeHtml(formatSalary(job.salary))}</strong></div>
        <div><span>Work mode</span><strong>${escapeHtml(workModeLabel(job.work_mode))}</strong></div>
        <div class="detail-grid-full"><span>Application deadline</span><strong>${escapeHtml(job.application_deadline || "Not specified")}</strong></div>
      </div>
      <div class="detail-description">${job.description ? renderMarkdown(job.description) : "<p>No detailed description is available for this job.</p>"}</div>
    `;
  } catch (error) {
    if (dialog.open) dialog.close();
    syncDialogScrollLock();
    showErrorDialog(error, { title: "WaterlooWorks job details unavailable" });
  }
}
let wwPollTimer = null;
function scheduleWaterlooWorksPoll() {
  const delay = wwBusy ? 2000 : 10000;
  wwPollTimer = setTimeout(() => {
    if ($("#tab-waterlooworks")?.classList.contains("active")) loadWaterlooWorksStatus();
    scheduleWaterlooWorksPoll();
  }, delay);
}
scheduleWaterlooWorksPoll();

export { loadWaterlooWorksStatus, loadWaterlooWorksJobs, openWaterlooWorksJob };
