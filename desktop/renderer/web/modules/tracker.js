import { $, $$, escapeHtml, formatRelativeTime, renderMarkdown, responseErrorMessage, showFieldError, clearFieldError, showToast, confirmDecision, createActionFeedback, announceStatus } from "./helpers.js?v=20260901-error-dialog-minimal-v1";
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

const TRACKER_FILTER_KEY = "wecan_tracker_filters_v2";
const trackerSaveFeedback = createActionFeedback("#save-tracker-drawer", { scope: "#tracker-detail-drawer" });
const trackerCopyFeedback = createActionFeedback("#drawer-copy-job", {
  scope: "#tracker-detail-drawer", pending: "Copying…", success: "Copied", resetAfter: 1800,
});
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
  const query = $("#tracker-query");
  if (query) {
    query.value = url.searchParams.get("tq") ?? stored.query ?? "";
    query.dispatchEvent(new Event("application:fill"));
  }
  const sort = $("#tracker-sort");
  if (sort) {
    sort.value = url.searchParams.get("tsort") ?? `${stored.sort || "updated"}:${stored.direction || "desc"}`;
    if (sort.selectedIndex < 0) sort.selectedIndex = 0;
    sort.dispatchEvent(new Event("change", { bubbles: true }));
  }
}

async function fetchTrackerData({ keepSelection = false } = {}) {
  const requestVersion = ++trackerState.requestVersion;
  trackerState.loading = true;
  $("#tracker-list-view").setAttribute("aria-busy", "true");
  $("#tracker-page-summary").textContent = "Loading applications…";
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
    if (requestVersion !== trackerState.requestVersion) return;
    $("#tracker-page-summary").textContent = "Applications could not be loaded";
    console.error("Tracker fetch error:", error);
    showToast("Could not load applications. Try again.", { variant: "error" });
  } finally {
    if (requestVersion === trackerState.requestVersion) {
      trackerState.loading = false;
      $("#tracker-list-view").setAttribute("aria-busy", "false");
    }
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

  updatePipelineSelection();
}

function updatePipelineSelection() {
  const currentStage = trackerState.stageFilter || "";
  $$(".pipeline-stage[data-stat-stage]").forEach((card) => {
    const selected = card.dataset.statStage === currentStage;
    card.classList.toggle("active", selected);
    card.setAttribute("aria-pressed", String(selected));
    if (selected) $("#tracker-ledger-title").textContent = currentStage ? card.querySelector("[data-stage-label]").textContent : "Applications";
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
  const detailId = $("#drawer-app-id").value;
  body.innerHTML = items.map((app) => {
    return `<tr class="tracker-row" data-app-id="${app.id}"${detailId === app.id ? ' aria-current="true"' : ""}>
      <td class="select-col"><input class="tracker-row-check" data-app-id="${app.id}" type="checkbox" aria-label="Select ${escapeHtml(app.title)}" ${trackerState.selectedIds.has(app.id) ? "checked" : ""} /></td>
      <td class="tracker-role"><button class="tracker-role-cell" type="button" aria-controls="tracker-detail-drawer"><strong>${escapeHtml(app.title)}</strong><span>${escapeHtml(app.company_name)}</span></button></td>
      <td><select class="tracker-inline-stage stage-${escapeHtml(app.stage)}" data-app-id="${app.id}" aria-label="Change stage for ${escapeHtml(app.title)}">${trackerStageOptions(app.stage)}</select></td>
      <td class="tracker-place"><span class="tracker-cell-text">${escapeHtml(app.location_text || "Unspecified")}</span><small>${escapeHtml(TRACKER_SOURCE_LABELS[app.source] || "Other")}</small></td>
      <td class="tracker-applied"><input class="tracker-inline-date" data-app-id="${app.id}" type="date" value="${toDateInput(app.applied_at)}" aria-label="Applied date for ${escapeHtml(app.title)}" /></td>
      <td class="tracker-updated"><span title="${escapeHtml(new Date(app.updated_at).toLocaleString())}">${escapeHtml(formatRelativeTime(app.updated_at))}</span></td>
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
  if (bulkStage) {
    bulkStage.innerHTML = trackerStageOptions("", { placeholder: "Move to…" });
    bulkStage.dispatchEvent(new Event("application:fill"));
  }
  for (const field of [customStage, drawerStage]) {
    if (!field) continue;
    field.innerHTML = trackerStageOptions(field.value || "interested");
    field.dispatchEvent(new Event("application:fill"));
  }
  for (const field of [customSource, drawerSource]) {
    if (field) {
      field.innerHTML = trackerSourceOptions(field.value || "other");
      field.dispatchEvent(new Event("application:fill"));
    }
  }
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
  const valid = isApplicationLink(url);
  $("#drawer-copy-job").disabled = !url;
  $("#drawer-open-job").href = valid ? url : "#";
  $("#drawer-open-job").hidden = !valid;
}

async function trackerRequest(url, options, failure) {
  try {
    const response = await fetch(url, options);
    if (!response.ok) throw new Error(await responseErrorMessage(response, failure));
    return response;
  } catch (error) {
    showToast(error instanceof TypeError ? failure : error.message || failure, { variant: "error" });
    return null;
  }
}

function isApplicationLink(value) {
  try { return ["http:", "https:"].includes(new URL(value).protocol); }
  catch { return false; }
}

function validateApplicationFields(root) {
  let valid = true;
  for (const field of root.querySelectorAll("input[required], input[type=url], input[type=date]")) {
    if (field.disabled) continue;
    const value = field.value.trim();
    let message = "";
    if (field.required && !value) message = field.id.includes("company") ? "Enter a company name." : "Enter a job title.";
    else if (field.required && value.length > 255) message = "Use 255 characters or fewer.";
    else if (field.type === "url" && value && !isApplicationLink(value)) message = "Enter a complete http or https link.";
    else if (field.type === "date" && field.validity.badInput) message = "Enter a complete date.";
    if (message) {
      showFieldError(field, message, { focus: valid });
      valid = false;
    } else clearFieldError(field);
  }
  return valid;
}

let openingRecord = 0;
const savingRecords = new Set();
function showRecordHeading(app) {
  $("#drawer-title").textContent = app.title;
  $("#drawer-company").textContent = app.company_name;
  $("#drawer-location-label").textContent = app.location_text || "";
  $("#tracker-detail-drawer").dataset.stage = app.stage;
}

async function openTrackerDrawer(appId) {
  const opening = ++openingRecord;
  let app = trackerState.applications.find((item) => item.id === appId);
  if (!app) {
    const response = await trackerRequest(`/api/v1/tracker/${appId}`, {}, "Could not load this application.");
    if (!response) return;
    app = await response.json();
  }
  if (opening !== openingRecord) return;
  $("#drawer-app-id").value = app.id;
  trackerSaveFeedback.reset({ cancelPending: true });
  trackerCopyFeedback.reset({ cancelPending: true });
  $("#save-tracker-drawer").disabled = savingRecords.has(app.id);
  showRecordHeading(app);
  const isPlatformJob = app.origin_type === "platform_bookmark";
  const isWaterlooJob = app.source === "waterloo_work";
  const notice = $("#drawer-origin-notice");
  notice.hidden = !isPlatformJob;
  notice.textContent = isPlatformJob ? `Job details from ${isWaterlooJob ? "WaterlooWorks" : "WeCanFindIntern"} are read-only.` : "";
  for (const invalid of $$("#tracker-detail-drawer [aria-invalid=true]")) clearFieldError(invalid);
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
  $("#drawer-stage").dispatchEvent(new Event("application:fill"));
  $("#tracker-detail-drawer").dispatchEvent(new Event("application:fill"));
  $("#tracker-detail-drawer").dispatchEvent(new Event("application:details"));
  $("#tracker-detail-drawer .record-posting").open = false;
  syncTrackerLinkActions();
  $("#tracker-detail-drawer").classList.add("open");
  $("#tracker-detail-drawer").setAttribute("aria-hidden", "false");
  $$(".tracker-row").forEach((row) => {
    if (row.dataset.appId === app.id) row.setAttribute("aria-current", "true");
    else row.removeAttribute("aria-current");
  });
  $(".tracker-drawer-body").scrollTop = 0;
  $("#close-tracker-drawer").focus({ preventScroll: true });
  await loadTrackerTimeline(app.id);
}

function closeTrackerDrawer() {
  openingRecord++;
  trackerSaveFeedback.reset({ cancelPending: true });
  trackerCopyFeedback.reset({ cancelPending: true });
  $("#tracker-detail-drawer").classList.remove("open");
  $("#tracker-detail-drawer").setAttribute("aria-hidden", "true");
  const current = $('.tracker-row[aria-current="true"]');
  $("#drawer-app-id").value = "";
  current?.removeAttribute("aria-current");
  (current?.querySelector(".tracker-role-cell") || $("#tracker-query")).focus({ preventScroll: true });
}

async function saveTrackerDrawer() {
  const appId = $("#drawer-app-id").value;
  if (!appId || savingRecords.has(appId) || !validateApplicationFields($("#tracker-detail-drawer"))) return;
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
  savingRecords.add(appId);
  const finish = trackerSaveFeedback.begin();
  try {
    const response = await trackerRequest(`/api/v1/tracker/${appId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }, "Could not save this application.");
    if (!response) return;
    const saved = await response.json();
    if ($("#drawer-app-id").value === appId) showRecordHeading(saved);
    finish(true);
    await fetchTrackerData({ keepSelection: true });
    if ($("#drawer-app-id").value === appId) await loadTrackerTimeline(appId);
  } finally {
    savingRecords.delete(appId);
    finish(false);
  }
}

function refreshOpenProgress(appId, changes) {
  if ($("#drawer-app-id").value !== appId) return;
  if ("stage" in changes) {
    $("#drawer-stage").value = changes.stage;
    $("#drawer-stage").dispatchEvent(new Event("application:fill"));
    $("#tracker-detail-drawer").dataset.stage = changes.stage;
  }
  if ("applied_at" in changes) $("#drawer-applied-at").value = toDateInput(changes.applied_at);
  void loadTrackerTimeline(appId);
}

async function loadTrackerTimeline(appId) {
  const root = $("#tracker-timeline");
  root.innerHTML = `<p class="tracker-view-status">Loading progress…</p>`;
  const response = await trackerRequest(`/api/v1/tracker/${appId}/events`, {}, "Could not load application progress.");
  if ($("#drawer-app-id").value !== appId) return;
  if (!response) { root.replaceChildren(); return; }
  const events = await response.json();
  if ($("#drawer-app-id").value !== appId) return;
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
            ${event.details ? `<p>${escapeHtml(event.details)}</p>` : ""}
          </div>
        </article>`;
    }).join("")
    : `<p class="tracker-view-status">No progress recorded yet.</p>`;
}

async function bulkUpdateTracker(payload) {
  const ids = [...trackerState.selectedIds];
  if (!ids.length) return;
  const response = await trackerRequest("/api/v1/tracker/bulk", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids, ...payload }) }, "Could not update the selected applications.");
  if (!response) return;
  trackerState.selectedIds.clear();
  const current = $("#drawer-app-id").value;
  if (ids.includes(current)) refreshOpenProgress(current, payload);
  await fetchTrackerData();
}


async function deleteTrackedApplication(appId) {
  const confirmed = await confirmDecision({
    title: "Delete application?",
    message: `“${$("#drawer-title").textContent}” and its activity will be permanently deleted.`,
    confirmLabel: "Delete application",
    destructive: true,
  });
  if (!confirmed) return;
  const response = await trackerRequest(`/api/v1/tracker/${appId}`, { method: "DELETE" }, "Could not delete this application.");
  if (!response) return;
  announceStatus("Application deleted.");
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
$$(".pipeline-stage[data-stat-stage]").forEach((card) => {
  card.addEventListener("click", () => {
    trackerState.stageFilter = card.dataset.statStage;
    updatePipelineSelection();
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
  if (appliedDate?.validity.badInput) {
    showFieldError(appliedDate, "Enter a complete applied date.");
    return;
  }
  const appId = (stage || appliedDate).dataset.appId;
  const payload = stage
    ? { stage: stage.value }
    : { applied_at: appliedDate.value ? new Date(`${appliedDate.value}T12:00:00`).toISOString() : null };
  const response = await trackerRequest(`/api/v1/tracker/${appId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }, "Could not update this application.");
  if (response) refreshOpenProgress(appId, payload);
  await fetchTrackerData({ keepSelection: true });
});
$("#tracker-select-page")?.addEventListener("change", (event) => { trackerState.applications.forEach((app) => event.target.checked ? trackerState.selectedIds.add(app.id) : trackerState.selectedIds.delete(app.id)); renderTrackerList(); renderTrackerPagination(); });
$("#tracker-clear-selection")?.addEventListener("click", () => { trackerState.selectedIds.clear(); renderTrackerList(); renderTrackerPagination(); });
$("#tracker-bulk-stage")?.addEventListener("change", (event) => { if (event.target.value) bulkUpdateTracker({ stage: event.target.value }); event.target.value = ""; event.target.dispatchEvent(new Event("application:fill")); });
$("#tracker-bulk-delete")?.addEventListener("click", async () => {
  const ids = [...trackerState.selectedIds];
  if (!ids.length) return;
  const count = `${ids.length} application${ids.length === 1 ? "" : "s"}`;
  const confirmed = await confirmDecision({
    title: `Delete ${count}?`, message: "The selected records and their activity will be permanently deleted.",
    confirmLabel: `Delete ${count}`, destructive: true,
  });
  if (!confirmed) return;
  const response = await trackerRequest("/api/v1/tracker/bulk", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids }) }, "Could not delete the selected applications.");
  if (!response) return;
  if (ids.includes($("#drawer-app-id").value)) closeTrackerDrawer();
  trackerState.selectedIds.clear();
  announceStatus(`Deleted ${count}.`);
  await fetchTrackerData();
});

$("#close-tracker-drawer")?.addEventListener("click", closeTrackerDrawer);
$("#tracker-detail-drawer")?.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeTrackerDrawer();
});
$("#save-tracker-drawer")?.addEventListener("click", saveTrackerDrawer);
$("#drawer-url")?.addEventListener("input", syncTrackerLinkActions);
$("#drawer-copy-job")?.addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const url = $("#drawer-url").value.trim();
  if (!url) return;
  const finish = trackerCopyFeedback.begin();
  let copied = false;
  try {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard API unavailable");
      await navigator.clipboard.writeText(url);
      copied = true;
    } catch (_) {
      const fallback = document.createElement("textarea");
      fallback.value = url; fallback.style.position = "fixed"; fallback.style.opacity = "0";
      document.body.appendChild(fallback);
      try { fallback.select(); copied = document.execCommand("copy"); }
      finally { fallback.remove(); }
    }
  } catch (_) {
    copied = false;
  } finally {
    finish(copied);
    if (button.isConnected && $("#tracker-detail-drawer").classList.contains("open")) button.focus({ preventScroll: true });
  }
  if (!copied) showToast("Could not copy the link. Select the URL and copy it manually.", { variant: "error" });
});
$("#btn-delete-tracked-app")?.addEventListener("click", () => { const id = $("#drawer-app-id").value; if (id) deleteTrackedApplication(id); });
function resetCustomApplication() {
  const form = $("#custom-job-form");
  form.reset();
  $("#custom-stage").value = "interested";
  $("#custom-source").value = "other";
  $("#custom-stage").dispatchEvent(new Event("application:fill"));
  form.dispatchEvent(new Event("application:reset"));
  form.querySelector(".record-posting").open = false;
  for (const field of form.querySelectorAll("[aria-invalid=true]")) clearFieldError(field);
}
$("#btn-cancel-custom-job")?.addEventListener("click", resetCustomApplication);
$("#custom-job-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!validateApplicationFields(event.currentTarget)) return;
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
  submitButton.disabled = true;
  try {
    const response = await fetch("/api/v1/tracker", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!response.ok) throw new Error(await responseErrorMessage(response, "Could not save this application."));
    const saved = await response.json();
    $("#custom-job-dialog")?.hidePopover();
    resetCustomApplication();
    announceStatus("Application added.");
    await fetchTrackerData();
    await openTrackerDrawer(saved.id);
  } catch (error) {
    showToast(error instanceof TypeError ? "Could not add this application." : error.message, { variant: "error" });
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
