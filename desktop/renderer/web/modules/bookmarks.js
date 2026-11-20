import { $, $$, responseErrorMessage, showToast, confirmDecision, announceStatus } from "./helpers.js?v=20260901-error-dialog-minimal-v1";
import { trackerStageLabel } from "./tracker-contract.js";

const bookmarkState = {
  publicJobs: new Map(),
  waterlooWorksJobs: new Map(),
};

const BOOKMARK_OUTLINE = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>`;
const BOOKMARK_FILLED = `<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>`;

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
    else if (tracked.stage === "interested") button.title = "Saved to Interested · Click to remove";
    else button.title = `${trackerStageLabel(tracked.stage)} in Tracker · Click to view`;
    button.setAttribute("aria-label", button.title);
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
  if (existing?.stage === "interested") {
    const remove = await confirmDecision({
      title: "Remove from Interested?",
      message: "This will delete the application and its activity from your tracker.",
      confirmLabel: "Remove application",
      destructive: true,
    });
    if (!remove) return;
  }
  const buttons = $$(buttonSelector);
  buttons.forEach((button) => { button.disabled = true; });
  try {
    if (!existing) {
      const response = await fetch(endpoint, { method: "PUT" });
      if (!response.ok) throw new Error(await responseErrorMessage(response, "Could not save this job to your tracker."));
      const app = await response.json();
      state.set(id, storedState(app));
      updateBookmarkButtons();
      announceStatus("Saved to Interested.");
      notifyTrackerChanged();
    } else if (existing.stage === "interested") {
      const response = await fetch(endpoint, { method: "DELETE" });
      if (!response.ok) throw new Error(await responseErrorMessage(response, "Could not remove this bookmark."));
      state.delete(id);
      updateBookmarkButtons();
      announceStatus("Removed from Interested.");
      notifyTrackerChanged();
    } else {
      openTracker(existing.application_id);
    }
  } catch (error) {
    showToast(error.message || "Could not update this bookmark.", { variant: "error" });
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
