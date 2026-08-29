import { $, escapeHtml, formatDate, formatRelativeTime } from "./helpers.js";

let wwBusy = false;

// =========================================================
// WATERLOOWORKS LOCAL IMPORT
// =========================================================

const WW_STATUS_LABELS = {
  idle: "Not connected",
  browser_starting: "Starting Chrome",
  waiting_for_login: "Waiting for login",
  waiting_for_job_page: "Open job search",
  ready: "Connected",
  collecting: "Collecting",
  importing: "Importing",
  completed: "Completed",
  partial: "Partially completed",
  failed: "Needs attention",
};

function renderWaterlooWorksStatus(data) {
  const status = data.status || "idle";
  wwBusy = ["collecting", "importing"].includes(status);
  const pill = $("#ww-status-pill");
  pill.className = `ww-status-pill ${status}`;
  pill.querySelector("span").textContent = WW_STATUS_LABELS[status] || status;
  $("#ww-status-title").textContent = WW_STATUS_LABELS[status] || "WaterlooWorks";
  $("#ww-status-message").textContent = data.message || "";
  $("#ww-status-icon").classList.toggle("active", ["ready", "collecting", "importing", "completed", "partial"].includes(status));
  $("#ww-unique-count").textContent = Number(data.unique_job_count || 0).toLocaleString();
  $("#ww-posting-success-count").textContent = Number(data.posting_success_count || 0).toLocaleString();
  $("#ww-posting-failed-count").textContent = Number(data.posting_failed_count || 0).toLocaleString();
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
  $("#ww-view-jobs").hidden = !["completed", "partial"].includes(status);
  for (const board of data.boards || []) {
    const row = document.querySelector(`[data-ww-board="${board.name}"]`);
    if (!row) continue;
    const stateLabel = row.querySelector(".ww-board-state");
    const metricsLabel = row.querySelector(".ww-board-metrics");
    const errorLabel = row.querySelector(".ww-board-error");
    const labels = {
      pending: "Pending",
      collecting: "Collecting…",
      completed: "Completed",
      failed: "Failed",
    };
    stateLabel.className = `ww-board-state ${board.status}`;
    stateLabel.textContent = labels[board.status] || board.status;
    metricsLabel.textContent = `${Number(board.discovered_count || 0).toLocaleString()} found · ${Number(board.posting_success_count || 0).toLocaleString()} succeeded · ${Number(board.posting_failed_count || 0).toLocaleString()} failed`;
    errorLabel.textContent = board.error || "";
    errorLabel.hidden = !board.error;
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
    const response = await fetch(`/api/v1/waterlooworks/jobs?${params}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not load WaterlooWorks jobs.");
    $("#ww-jobs-total").textContent = `${Number(data.total || 0).toLocaleString()} jobs`;
    if (!data.items?.length) {
      list.innerHTML = '<p class="muted-copy">No matching WaterlooWorks jobs.</p>';
      return;
    }
    list.innerHTML = data.items.map((job) => `
      <article class="ww-job-item">
        <div>
          <span class="ww-job-id">Job ID ${escapeHtml(job.source_job_id || "Unknown")}</span>
          <h3>${escapeHtml(job.title)}</h3>
          <p>${escapeHtml(job.organization || "Organization not specified")} · ${escapeHtml(job.location_text || "Location not specified")}</p>
        </div>
        <div class="ww-job-item-meta">
          <span>${escapeHtml((job.boards || []).map((value) => value.replaceAll("_", " ")).join(" · "))}</span>
          <span>${escapeHtml(formatDate(job.date_posted))}</span>
        </div>
      </article>
    `).join("");
  } catch (error) {
    list.innerHTML = `<p class="notice error">${escapeHtml(error.message)}</p>`;
  }
}

async function loadWaterlooWorksStatus() {
  try {
    const response = await fetch("/api/v1/waterlooworks/status");
    if (!response.ok) throw new Error("Could not read WaterlooWorks status.");
    renderWaterlooWorksStatus(await response.json());
    $("#ww-action-error").hidden = true;
  } catch (error) {
    $("#ww-action-error").textContent = error.message;
    $("#ww-action-error").hidden = false;
  }
}

async function runWaterlooWorksAction(path) {
  const errorBox = $("#ww-action-error");
  errorBox.hidden = true;
  try {
    const response = await fetch(`/api/v1/waterlooworks/${path}`, { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `WaterlooWorks action failed (${response.status}).`);
    renderWaterlooWorksStatus(data);
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  }
}

$("#ww-launch")?.addEventListener("click", () => runWaterlooWorksAction("launch"));
$("#ww-collect")?.addEventListener("click", () => runWaterlooWorksAction("collect"));
$("#ww-view-jobs")?.addEventListener("click", () => {
  loadWaterlooWorksJobs();
  $("#ww-jobs-card").scrollIntoView({ behavior: "smooth", block: "start" });
});
$("#ww-job-search")?.addEventListener("click", loadWaterlooWorksJobs);
$("#ww-job-board")?.addEventListener("change", loadWaterlooWorksJobs);
$("#ww-job-query")?.addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadWaterlooWorksJobs();
});
let wwPollTimer = null;
function scheduleWaterlooWorksPoll() {
  const delay = wwBusy ? 2000 : 10000;
  wwPollTimer = setTimeout(() => {
    if ($("#tab-waterlooworks")?.classList.contains("active")) loadWaterlooWorksStatus();
    scheduleWaterlooWorksPoll();
  }, delay);
}
scheduleWaterlooWorksPoll();

export { loadWaterlooWorksStatus, loadWaterlooWorksJobs };
