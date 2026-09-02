import { $, $$, escapeHtml, formatRelativeTime, renderMarkdown, responseErrorMessage, showErrorDialog } from "./helpers.js?v=20260901-error-dialog-minimal-v1";
import {
  loadWaterlooWorksBookmarks,
  setPublicBookmarks,
  setWaterlooWorksBookmarks,
  toggleBookmarkJob,
  toggleWaterlooWorksBookmark,
  updateBookmarkButtons,
} from "./bookmarks.js";
import {
  TRACKER_SOURCE_LABELS,
  TRACKER_STAGE_LABELS,
  loadTrackerContract,
  trackerSourceOptions,
  trackerStageOptions,
} from "./tracker-contract.js";

const trackerState = {
  applications: [],
  stats: {},
  selectedIds: new Set(),
  loading: false,
  page: 1,
  pageSize: 50,
  total: 0,
  totalPages: 0,
  requestVersion: 0,
};

function mountApplicationsWorkspace() {
  const dashboard = $("#tab-tracker .tracker-dashboard");
  const drawer = $("#tracker-detail-drawer");
  if (!dashboard || !drawer || dashboard.querySelector(".applications-list-pane")) return;

  const listPane = document.createElement("div");
  listPane.className = "applications-list-pane";
  Array.from(dashboard.children).forEach((child) => listPane.appendChild(child));

  const emptyDetail = document.createElement("div");
  emptyDetail.className = "applications-detail-empty";
  emptyDetail.innerHTML = `<span aria-hidden="true">✓</span><h2>Select an application</h2><p>Choose an opportunity to review its status, job information, and timeline.</p>`;

  drawer.classList.add("applications-detail-pane");
  dashboard.append(listPane, drawer, emptyDetail);
}

mountApplicationsWorkspace();

// =========================================================
// SCALABLE APPLICATION TRACKER WORKSPACE
// =========================================================

const TRACKER_FILTER_KEY = "wecan_tracker_filters_v2";
function trackerFiltersFromControls() {
  const [sort, direction] = ($("#tracker-sort")?.value || "updated:desc").split(":");
  return {
    query: $("#tracker-query")?.value.trim() || "",
    stage: trackerState.stageFilter || "",
    sort,
    direction,
  };
}

function buildTrackerParams() {
  const filters = trackerFiltersFromControls();
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => value && params.set(key, value));
  params.set("page", String(trackerState.page));
  params.set("page_size", String(trackerState.pageSize));
  return params;
}

function syncTrackerFilterState() {
  const filters = trackerFiltersFromControls();
  localStorage.setItem(TRACKER_FILTER_KEY, JSON.stringify({ query: filters.query, stage: filters.stage, sort: filters.sort, direction: filters.direction }));
  const url = new URL(window.location.href);
  ["tq", "tstage", "tsort"].forEach((key) => url.searchParams.delete(key));
  if (filters.query) url.searchParams.set("tq", filters.query);
  if (filters.stage) url.searchParams.set("tstage", filters.stage);
  if (`${filters.sort}:${filters.direction}` !== "updated:desc") url.searchParams.set("tsort", `${filters.sort}:${filters.direction}`);
  history.replaceState({}, "", url);
  const exportParams = new URLSearchParams();
  if (filters.query) exportParams.set("query", filters.query);
  if (filters.stage) exportParams.set("stage", filters.stage);
  const exportLink = $("#tracker-export");
  if (exportLink) exportLink.href = `/api/v1/tracker/export.csv?${exportParams}`;
}

function restoreTrackerFilters() {
  let stored = {};
  try { stored = JSON.parse(localStorage.getItem(TRACKER_FILTER_KEY) || "{}"); } catch (_) { stored = {}; }
  const url = new URL(window.location.href);
  trackerState.stageFilter = url.searchParams.get("tstage") ?? stored.stage ?? "";
  if ($("#tracker-query")) $("#tracker-query").value = url.searchParams.get("tq") ?? stored.query ?? "";
  if ($("#tracker-sort")) $("#tracker-sort").value = url.searchParams.get("tsort") ?? `${stored.sort || "updated"}:${stored.direction || "desc"}`;
}

async function fetchTrackerData({ keepSelection = false } = {}) {
  const requestVersion = ++trackerState.requestVersion;
  trackerState.loading = true;
  try {
    syncTrackerFilterState();
    const params = buildTrackerParams();
    const [listRes, bookmarksRes, wwBookmarksRes] = await Promise.all([
      fetch(`/api/v1/tracker?${params}`),
      fetch("/api/v1/tracker/bookmarks"),
      fetch("/api/v1/tracker/bookmarks/waterlooworks"),
    ]);
    if (!listRes.ok) throw new Error("Failed to load applications");
    const data = await listRes.json();
    if (requestVersion !== trackerState.requestVersion) return;
    trackerState.applications = data.items || [];
    trackerState.stats = data.stats || {};
    trackerState.total = data.total || 0;
    trackerState.totalPages = data.total_pages || 0;
    if (bookmarksRes.ok) {
      const bookmarks = await bookmarksRes.json();
      setPublicBookmarks(bookmarks);
    }
    if (wwBookmarksRes.ok) {
      const wwBookmarks = await wwBookmarksRes.json();
      setWaterlooWorksBookmarks(wwBookmarks);
    }
    if (!keepSelection) trackerState.selectedIds.clear();
    renderTrackerStats();
    renderTrackerList();
    renderTrackerPagination();
    updateBookmarkButtons();
  } catch (error) {
    console.error("Tracker fetch error:", error);
    showErrorDialog(error, { title: "Application Tracker unavailable" });
  } finally {
    if (requestVersion === trackerState.requestVersion) trackerState.loading = false;
  }
}

function renderTrackerStats() {
  const s = trackerState.stats;
  const values = {
    "#stat-total": s.total,
    "#stat-interested": s.interested_count,
    "#stat-applied": s.applied_count,
    "#stat-interview": s.interview_count,
    "#stat-offer": s.offer_count,
    "#stat-rejected": s.rejected_count,
  };
  Object.entries(values).forEach(([selector, value]) => { if ($(selector)) $(selector).textContent = Number(value || 0).toLocaleString(); });
  if ($("#stat-rate")) $("#stat-rate").textContent = `${s.response_rate_percent || 0}%`;

  const currentStage = trackerState.stageFilter || "";
  $$(".tracker-stat-card[data-stat-stage]").forEach((card) => {
    card.classList.toggle("active", (card.dataset.statStage ?? "") === currentStage);
  });
}

function trackerDate(value, fallback = "—") {
  if (!value) return fallback;
  const date = new Date(value.length === 10 ? `${value}T12:00:00` : value);
  return Number.isNaN(date.getTime()) ? fallback : new Intl.DateTimeFormat("en-CA", { month: "short", day: "numeric", year: "numeric" }).format(date);
}

function renderTrackerList() {
  const body = $("#tracker-table-body");
  if (!body) return;
  const items = trackerState.applications;
  $("#tracker-empty").hidden = Boolean(items.length);
  body.innerHTML = items.map((app) => {
    return `<tr class="tracker-row" data-app-id="${app.id}">
      <td class="select-col"><input class="tracker-row-check" data-app-id="${app.id}" type="checkbox" aria-label="Select ${escapeHtml(app.title)}" ${trackerState.selectedIds.has(app.id) ? "checked" : ""} /></td>
      <td><div class="tracker-role-cell"><strong>${escapeHtml(app.company_name)}</strong><span>${escapeHtml(app.title)}</span></div></td>
      <td><select class="tracker-inline-stage stage-${escapeHtml(app.stage)}" data-app-id="${app.id}" aria-label="Change stage for ${escapeHtml(app.title)}">${trackerStageOptions(app.stage)}</select></td>
      <td><span class="tracker-cell-text">${escapeHtml(app.location_text || "Unspecified")}</span></td>
      <td><span class="tracker-cell-text">${escapeHtml(TRACKER_SOURCE_LABELS[app.source] || "Other")}</span></td>
      <td><input class="tracker-inline-date" data-app-id="${app.id}" type="date" value="${toDateInput(app.applied_at)}" aria-label="Applied date for ${escapeHtml(app.title)}" /></td>
      <td><span title="${escapeHtml(new Date(app.updated_at).toLocaleString())}">${escapeHtml(formatRelativeTime(app.updated_at))}</span></td>
    </tr>`;
  }).join("");
  updateBulkBar();
}

function initializeTrackerContractControls() {
  const bulkStage = $("#tracker-bulk-stage");
  const customStage = $("#custom-stage");
  const drawerStage = $("#drawer-stage");
  const customSource = $("#custom-source");
  const drawerSource = $("#drawer-source");
  if (bulkStage) bulkStage.innerHTML = trackerStageOptions("", { placeholder: "Move to…" });
  if (customStage) customStage.innerHTML = trackerStageOptions("interested");
  if (drawerStage) drawerStage.innerHTML = trackerStageOptions("interested");
  if (customSource) customSource.innerHTML = trackerSourceOptions("other");
  if (drawerSource) drawerSource.innerHTML = trackerSourceOptions("other");
}

initializeTrackerContractControls();
loadTrackerContract()
  .then(initializeTrackerContractControls)
  .catch((error) => console.warn("Using fallback Tracker contract:", error));

function renderTrackerPagination() {
  const start = trackerState.total ? (trackerState.page - 1) * trackerState.pageSize + 1 : 0;
  const end = Math.min(trackerState.page * trackerState.pageSize, trackerState.total);
  $("#tracker-page-summary").textContent = `${start}–${end} of ${trackerState.total.toLocaleString()}`;
  $("#tracker-page-prev").disabled = trackerState.page <= 1;
  $("#tracker-page-next").disabled = trackerState.page >= trackerState.totalPages;
  $("#tracker-select-page").checked = trackerState.applications.length > 0 && trackerState.applications.every((app) => trackerState.selectedIds.has(app.id));
}

function updateBulkBar() {
  const count = trackerState.selectedIds.size;
  $("#tracker-bulk-bar").hidden = count === 0;
  $("#tracker-selected-count").textContent = count.toLocaleString();
}

function toDateInput(value) { return value ? String(value).slice(0, 10) : ""; }

function syncTrackerLinkActions() {
  const url = $("#drawer-url").value.trim();
  $("#drawer-copy-job").disabled = !url;
  $("#drawer-open-job").href = url || "#";
  $("#drawer-open-job").hidden = !url;
}

async function openTrackerDrawer(appId) {
  let app = trackerState.applications.find((item) => item.id === appId);
  if (!app) {
    const response = await fetch(`/api/v1/tracker/${appId}`);
    if (!response.ok) return;
    app = await response.json();
  }
  $("#drawer-app-id").value = app.id;
  $("#drawer-title").textContent = app.title;
  $("#drawer-company").textContent = `${app.company_name} · ${app.location_text || "Location unspecified"}`;
  const isPlatformJob = app.origin_type === "platform_bookmark";
  const isWaterlooJob = app.source === "waterloo_work";
  $("#drawer-origin-notice").textContent = isWaterlooJob
    ? "Bookmarked from WaterlooWorks · Job information was saved from WaterlooWorks and is read-only."
    : isPlatformJob
      ? "Bookmarked from WeCanFindIntern · Job information was saved from the platform and is read-only."
      : "External application · You can edit both the job information and tracking fields.";
  $("#drawer-origin-notice").classList.toggle("platform-origin", isPlatformJob);
  const fields = {
    "#drawer-stage": app.stage,
    "#drawer-applied-at": toDateInput(app.applied_at),
    "#drawer-source": app.source || "other",
    "#drawer-url": app.job_url || "",
    "#drawer-jd-input": app.job_description || "",
    "#drawer-company-input": app.company_name || "",
    "#drawer-title-input": app.title || "",
    "#drawer-location": app.location_text || "",
    "#drawer-work-mode": app.work_mode || "",
    "#drawer-salary": app.salary_text || "",
    "#drawer-deadline": toDateInput(app.application_deadline),
  };
  Object.entries(fields).forEach(([selector, value]) => { $(selector).value = value; });
  $("#drawer-external-status").textContent = app.external_status || "";
  $("#drawer-external-status-group").hidden = !app.external_status;
  $("#drawer-jd-readonly").innerHTML = app.job_description ? renderMarkdown(app.job_description) : "<p>No job description is available.</p>";
  $("#drawer-jd-readonly").hidden = !isPlatformJob;
  $("#drawer-jd-input").hidden = isPlatformJob;
  $$("#tracker-detail-drawer .job-content-field input, #tracker-detail-drawer .job-content-field select, #tracker-detail-drawer .job-content-field textarea").forEach((field) => {
    field.disabled = isPlatformJob;
  });
  $("#tracker-detail-drawer").dataset.originType = app.origin_type;
  syncTrackerLinkActions();
  $("#tracker-detail-drawer").classList.add("open");
  $("#tracker-detail-drawer").setAttribute("aria-hidden", "false");
  $("#tracker-drawer-backdrop").hidden = false;
  document.body.classList.add("modal-open");
  await loadTrackerTimeline(app.id);
}

function closeTrackerDrawer() {
  $("#tracker-detail-drawer").classList.remove("open");
  $("#tracker-detail-drawer").setAttribute("aria-hidden", "true");
  $("#tracker-drawer-backdrop").hidden = true;
  document.body.classList.remove("modal-open");
}

async function saveTrackerDrawer() {
  const appId = $("#drawer-app-id").value;
  if (!appId) return;
  const dateIso = (selector) => $(selector).value ? new Date(`${$(selector).value}T12:00:00`).toISOString() : null;
  const payload = {
    stage: $("#drawer-stage").value,
    applied_at: dateIso("#drawer-applied-at"),
  };
  if ($("#tracker-detail-drawer").dataset.originType === "custom") {
    Object.assign(payload, {
      job_description: $("#drawer-jd-input").value.trim() || null,
      company_name: $("#drawer-company-input").value.trim(),
      title: $("#drawer-title-input").value.trim(),
      location_text: $("#drawer-location").value.trim() || null,
      work_mode: $("#drawer-work-mode").value || null,
      salary_text: $("#drawer-salary").value.trim() || null,
      application_deadline: $("#drawer-deadline").value || null,
      source: $("#drawer-source").value,
      job_url: $("#drawer-url").value.trim() || null,
    });
  }
  const response = await fetch(`/api/v1/tracker/${appId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  if (!response.ok) { showErrorDialog(await responseErrorMessage(response, "Could not save this application."), { title: "Application update failed" }); return; }
  const saved = await response.json();
  $("#drawer-title").textContent = saved.title;
  $("#drawer-company").textContent = `${saved.company_name} · ${saved.location_text || "Location unspecified"}`;
  $("#drawer-save-status").hidden = false;
  setTimeout(() => { $("#drawer-save-status").hidden = true; }, 1600);
  await fetchTrackerData({ keepSelection: true });
  await loadTrackerTimeline(appId);
}

async function loadTrackerTimeline(appId) {
  const root = $("#tracker-timeline");
  root.innerHTML = `<p class="tracker-view-status">Loading progress…</p>`;
  const response = await fetch(`/api/v1/tracker/${appId}/events`);
  if (!response.ok) {
    root.innerHTML = "";
    showErrorDialog(await responseErrorMessage(response, "Application progress could not be loaded."), { title: "Timeline unavailable" });
    return;
  }
  const events = await response.json();
  root.innerHTML = events.length
    ? events.map((event) => {
      const typeClass = escapeHtml(event.event_type || "stage_change");
      const stageKey = (event.title || "").toLowerCase().replace(/[^a-z]/g, "");
      const stageClass = `stage-${escapeHtml(stageKey)}`;
      return `<article class="timeline-item">
          <div class="timeline-spine">
            <span class="timeline-marker event-${typeClass} ${stageClass}"></span>
          </div>
          <div class="timeline-content">
            <div class="timeline-header">
              <strong class="timeline-title">${escapeHtml(event.title)}</strong>
              <time class="timeline-time">${escapeHtml(trackerDate(event.occurred_at))}</time>
            </div>
          </div>
        </article>`;
    }).join("")
    : `<p class="tracker-view-status">No progress recorded yet.</p>`;
}

async function bulkUpdateTracker(payload) {
  const ids = [...trackerState.selectedIds];
  if (!ids.length) return;
  const response = await fetch("/api/v1/tracker/bulk", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids, ...payload }) });
  if (!response.ok) { showErrorDialog(await responseErrorMessage(response, "The selected applications could not be updated."), { title: "Bulk update failed" }); return; }
  trackerState.selectedIds.clear();
  await fetchTrackerData();
}


async function deleteTrackedApplication(appId) {
  if (!confirm("Delete this application and its activity permanently?")) return;
  const response = await fetch(`/api/v1/tracker/${appId}`, { method: "DELETE" });
  if (!response.ok) { showErrorDialog(await responseErrorMessage(response, "The application could not be deleted."), { title: "Delete failed" }); return; }
  closeTrackerDrawer();
  await fetchTrackerData();
}

let trackerSearchTimer;
function trackerFiltersChanged() {
  trackerState.page = 1;
  clearTimeout(trackerSearchTimer);
  trackerSearchTimer = setTimeout(() => fetchTrackerData(), 250);
}

restoreTrackerFilters();

$("#tracker-query")?.addEventListener("input", trackerFiltersChanged);
$("#tracker-sort")?.addEventListener("change", trackerFiltersChanged);
$$(".tracker-stat-card[data-stat-stage]").forEach((card) => {
  card.addEventListener("click", () => {
    trackerState.stageFilter = card.dataset.statStage ?? "";
    trackerState.page = 1;
    trackerFiltersChanged();
  });
});
$("#tracker-page-prev")?.addEventListener("click", () => { if (trackerState.page > 1) { trackerState.page--; fetchTrackerData(); } });
$("#tracker-page-next")?.addEventListener("click", () => { if (trackerState.page < trackerState.totalPages) { trackerState.page++; fetchTrackerData(); } });
$("#tracker-page-size")?.addEventListener("change", (event) => { trackerState.pageSize = Number(event.target.value); trackerState.page = 1; fetchTrackerData(); });

$("#tracker-table-body")?.addEventListener("click", (event) => {
  const check = event.target.closest(".tracker-row-check");
  if (check) { check.checked ? trackerState.selectedIds.add(check.dataset.appId) : trackerState.selectedIds.delete(check.dataset.appId); updateBulkBar(); renderTrackerPagination(); return; }
  if (event.target.closest("select,input")) return;
  const target = event.target.closest("[data-app-id]");
  if (target) openTrackerDrawer(target.dataset.appId);
});
$("#tracker-table-body")?.addEventListener("change", async (event) => {
  const stage = event.target.closest(".tracker-inline-stage");
  const appliedDate = event.target.closest(".tracker-inline-date");
  if (!stage && !appliedDate) return;
  const appId = (stage || appliedDate).dataset.appId;
  const payload = stage
    ? { stage: stage.value }
    : { applied_at: appliedDate.value ? new Date(`${appliedDate.value}T12:00:00`).toISOString() : null };
  const response = await fetch(`/api/v1/tracker/${appId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  if (!response.ok) { showErrorDialog(await responseErrorMessage(response, "This application could not be updated."), { title: "Application update failed" }); await fetchTrackerData({ keepSelection: true }); return; }
  await fetchTrackerData({ keepSelection: true });
});
$("#tracker-select-page")?.addEventListener("change", (event) => { trackerState.applications.forEach((app) => event.target.checked ? trackerState.selectedIds.add(app.id) : trackerState.selectedIds.delete(app.id)); renderTrackerList(); renderTrackerPagination(); });
$("#tracker-clear-selection")?.addEventListener("click", () => { trackerState.selectedIds.clear(); renderTrackerList(); renderTrackerPagination(); });
$("#tracker-bulk-stage")?.addEventListener("change", (event) => { if (event.target.value) bulkUpdateTracker({ stage: event.target.value }); event.target.value = ""; });
$("#tracker-bulk-delete")?.addEventListener("click", async () => {
  const ids = [...trackerState.selectedIds]; if (!ids.length || !confirm(`Delete ${ids.length} applications permanently?`)) return;
  const response = await fetch("/api/v1/tracker/bulk", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids }) });
  if (response.ok) { trackerState.selectedIds.clear(); fetchTrackerData(); }
  else showErrorDialog(await responseErrorMessage(response, "The selected applications could not be deleted."), { title: "Bulk delete failed" });
});

$("#close-tracker-drawer")?.addEventListener("click", closeTrackerDrawer);
$("#tracker-drawer-backdrop")?.addEventListener("click", closeTrackerDrawer);
$("#save-tracker-drawer")?.addEventListener("click", saveTrackerDrawer);
$("#drawer-url")?.addEventListener("input", syncTrackerLinkActions);
$("#drawer-copy-job")?.addEventListener("click", async (event) => {
  const url = $("#drawer-url").value.trim();
  if (!url) { event.target.textContent = "No link"; setTimeout(() => { event.target.textContent = "Copy"; }, 1200); return; }
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(url);
    else throw new Error("Clipboard API unavailable");
    event.target.textContent = "Copied";
  } catch (_) {
    const fallback = document.createElement("textarea");
    fallback.value = url; fallback.style.position = "fixed"; fallback.style.opacity = "0";
    document.body.appendChild(fallback); fallback.select();
    const copied = document.execCommand("copy"); fallback.remove();
    event.target.textContent = copied ? "Copied" : "Copy";
    if (!copied) showErrorDialog("The browser did not allow this link to be copied.", { title: "Copy failed", guidance: "Select and copy the application URL manually, or allow clipboard access and try again." });
  }
  setTimeout(() => { event.target.textContent = "Copy"; }, 1200);
});
$("#btn-delete-tracked-app")?.addEventListener("click", () => { const id = $("#drawer-app-id").value; if (id) deleteTrackedApplication(id); });
$("#btn-open-custom-job")?.addEventListener("click", () => { $("#custom-job-form").reset(); $("#custom-job-dialog")?.showModal(); syncDialogScrollLock(); });
$("#close-custom-job-dialog")?.addEventListener("click", () => $("#custom-job-dialog")?.close());
$("#btn-cancel-custom-job")?.addEventListener("click", () => $("#custom-job-dialog")?.close());
$("#custom-job-dialog")?.addEventListener("click", (event) => { if (event.target === $("#custom-job-dialog")) $("#custom-job-dialog")?.close(); });
$("#custom-job-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submitButton = event.currentTarget.querySelector("button[type='submit']");
  const payload = {
    company_name: $("#custom-company").value.trim(), title: $("#custom-title").value.trim(),
    location_text: $("#custom-location").value.trim() || null, work_mode: $("#custom-work-mode").value || null,
    stage: $("#custom-stage").value, salary_text: $("#custom-salary").value.trim() || null,
    application_deadline: $("#custom-deadline").value || null,
    source: $("#custom-source").value,
    applied_at: $("#custom-applied-at").value ? new Date(`${$("#custom-applied-at").value}T12:00:00`).toISOString() : null,
    job_url: $("#custom-url").value.trim() || null,
    job_description: $("#custom-job-description").value.trim() || null,
  };
  const missing = [!payload.company_name && "company name", !payload.title && "job title"].filter(Boolean);
  if (missing.length) {
    showErrorDialog(`Missing required fields: ${missing.join(", ")}.`, { title: "Application details incomplete", guidance: "Complete the required fields and save the application again." });
    return;
  }
  if (payload.job_url) {
    try { new URL(payload.job_url); } catch (_) {
      showErrorDialog("The Job Posting / Application URL is not a valid URL.", { title: "Invalid application link", guidance: "Enter a complete link such as https://careers.company.com/job/123, or leave the field empty." });
      return;
    }
  }
  submitButton.disabled = true;
  try {
    const response = await fetch("/api/v1/tracker", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!response.ok) throw new Error(await responseErrorMessage(response, "Could not save this application."));
    $("#custom-job-dialog")?.close();
    await fetchTrackerData();
  } catch (error) {
    showErrorDialog(error, { title: "Application could not be saved" });
  } finally {
    submitButton.disabled = false;
  }
});

export {
  fetchTrackerData,
  loadWaterlooWorksBookmarks,
  openTrackerDrawer,
  toggleBookmarkJob,
  toggleWaterlooWorksBookmark,
  trackerState,
  updateBookmarkButtons,
};
