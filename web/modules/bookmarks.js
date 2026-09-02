import { $, $$, responseErrorMessage, showErrorDialog } from "./helpers.js?v=20260901-error-dialog-minimal-v1";
import { trackerStageLabel } from "./tracker-contract.js";

const bookmarkState = {
  publicJobs: new Map(),
  waterlooWorksJobs: new Map(),
};

const BOOKMARK_OUTLINE = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>`;
const BOOKMARK_FILLED = `<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>`;

let plainToastTimer;
function showPlainToast(message, actionLabel = null, onAction = null) {
  let toast = $("#plain-toast");
  if (!toast) {
    document.body.insertAdjacentHTML(
      "beforeend",
      `<div id="plain-toast" class="plain-toast" hidden><span class="plain-toast-msg"></span><button class="plain-toast-action" type="button" hidden></button></div>`,
    );
    toast = $("#plain-toast");
  }
  const msgEl = toast.querySelector(".plain-toast-msg");
  const actionBtn = toast.querySelector(".plain-toast-action");
  msgEl.textContent = message;
  actionBtn.hidden = !(actionLabel && onAction);
  actionBtn.textContent = actionLabel || "";
  actionBtn.onclick = actionLabel && onAction
    ? () => {
      toast.hidden = true;
      toast.classList.remove("visible");
      onAction();
    }
    : null;
  toast.hidden = false;
  requestAnimationFrame(() => toast.classList.add("visible"));
  clearTimeout(plainToastTimer);
  plainToastTimer = setTimeout(() => {
    toast.classList.remove("visible");
    setTimeout(() => { toast.hidden = true; }, 200);
  }, 3200);
}

function setPublicBookmarks(items) {
  bookmarkState.publicJobs = new Map(items.map((item) => [item.job_id, item]));
}

function setWaterlooWorksBookmarks(items) {
  bookmarkState.waterlooWorksJobs = new Map(
    items.map((item) => [item.external_job_id, item]),
  );
}

async function loadPublicBookmarks() {
  const response = await fetch("/api/v1/tracker/bookmarks");
  if (!response.ok) return;
  setPublicBookmarks(await response.json());
  updateBookmarkButtons();
}

async function loadWaterlooWorksBookmarks() {
  const response = await fetch("/api/v1/tracker/bookmarks/waterlooworks");
  if (!response.ok) return;
  setWaterlooWorksBookmarks(await response.json());
  updateBookmarkButtons();
}

function updateButtons(selector, state, idKey) {
  $$(selector).forEach((button) => {
    const tracked = state.get(button.dataset[idKey]);
    const saved = Boolean(tracked);
    button.classList.toggle("saved", saved);
    button.setAttribute("aria-pressed", String(saved));
    button.innerHTML = saved ? BOOKMARK_FILLED : BOOKMARK_OUTLINE;
    if (!saved) button.title = "Save to Interested";
    else if (tracked.stage === "interested") button.title = "Interested · Click to remove";
    else button.title = `${trackerStageLabel(tracked.stage)} in Tracker · Click to view`;
  });
}

function updateBookmarkButtons() {
  updateButtons(".job-bookmark-btn", bookmarkState.publicJobs, "jobId");
  updateButtons(
    ".ww-bookmark-btn",
    bookmarkState.waterlooWorksJobs,
    "sourceJobId",
  );
}

function notifyTrackerChanged() {
  document.dispatchEvent(new CustomEvent("tracker:data-invalidated"));
}

function openTracker(applicationId = null) {
  document.dispatchEvent(new CustomEvent("tracker:open-requested", {
    detail: { applicationId },
  }));
}

async function toggleBookmark({ id, state, buttonSelector, endpoint, storedState }) {
  const existing = state.get(id);
  const buttons = $$(buttonSelector);
  buttons.forEach((button) => { button.disabled = true; });
  try {
    if (!existing) {
      const response = await fetch(endpoint, { method: "PUT" });
      if (!response.ok) throw new Error(await responseErrorMessage(response, "Could not save this job to your tracker."));
      const app = await response.json();
      state.set(id, storedState(app));
      updateBookmarkButtons();
      showPlainToast("Saved to Interested", "Open Tracker ↗", () => openTracker());
      notifyTrackerChanged();
    } else if (existing.stage === "interested") {
      const response = await fetch(endpoint, { method: "DELETE" });
      if (!response.ok) throw new Error(await responseErrorMessage(response, "Could not remove this bookmark."));
      state.delete(id);
      updateBookmarkButtons();
      showPlainToast("Removed from Interested");
      notifyTrackerChanged();
    } else {
      const stage = trackerStageLabel(existing.stage);
      showPlainToast(`This job is in stage [${stage}] in your Tracker`, "Open Tracker ↗", () => openTracker(existing.application_id));
    }
  } catch (error) {
    showErrorDialog(error, { title: "Tracker bookmark failed" });
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

async function toggleBookmarkJob(jobId) {
  return toggleBookmark({
    id: jobId,
    state: bookmarkState.publicJobs,
    buttonSelector: `.job-bookmark-btn[data-job-id="${jobId}"]`,
    endpoint: `/api/v1/tracker/bookmarks/${jobId}`,
    storedState: (app) => ({ job_id: jobId, application_id: app.id, stage: app.stage }),
  });
}

async function toggleWaterlooWorksBookmark(sourceJobId) {
  return toggleBookmark({
    id: sourceJobId,
    state: bookmarkState.waterlooWorksJobs,
    buttonSelector: `.ww-bookmark-btn[data-source-job-id="${sourceJobId}"]`,
    endpoint: `/api/v1/tracker/bookmarks/waterlooworks/${encodeURIComponent(sourceJobId)}`,
    storedState: (app) => ({
      external_job_id: sourceJobId,
      application_id: app.id,
      stage: app.stage,
    }),
  });
}

export {
  BOOKMARK_FILLED,
  BOOKMARK_OUTLINE,
  bookmarkState,
  loadPublicBookmarks,
  loadWaterlooWorksBookmarks,
  setPublicBookmarks,
  setWaterlooWorksBookmarks,
  toggleBookmarkJob,
  toggleWaterlooWorksBookmark,
  updateBookmarkButtons,
};
