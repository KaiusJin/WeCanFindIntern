import { $, fillGlassInput, confirmDecision, showSystemFault, showToast } from "./helpers.js?v=20260901-error-dialog-minimal-v1";
import { loadSettings } from "./settings.js?v=20260902-shared-components-v1";
import { initializeNavigation, setTabActivators, switchTab } from "./navigation.js?v=20260901-app-shell-v4";
import { jobContextState } from "./job-context.js?v=20260831-jobboard-parity-v3";
import { setupInfiniteScroll } from "./pagination.js?v=20260901-results-scroll-container-v1";
import { startDesktopJobSyncMonitor } from "./desktop-job-sync.js?v=20261119-job-sync-v2";
import {
  state,
  loadJobs,
  loadFacets,
  updateSliderFill,
  setupBackToTop,
} from "./jobs.js?v=20260902-shared-components-v1";

// Settings must be applied before any AI feature runs.
window.weCanFindInternDesktop?.onOperationError?.((title, message) => showToast(message, { title, variant: "error" }));
window.weCanFindInternDesktop?.onRestoreConfirmation?.(() => confirmDecision({
  title: "Replace the local database?",
  message: "The app will back up your current data, restore the selected backup, and restart. If the restore fails, the app will recover your current data from the safety backup.",
  confirmLabel: "Restore and restart",
  destructive: true,
}));
await loadSettings();

// Non-default tabs load only when activated. Several modules initialize their
// section state at import time, so speculative hover imports would otherwise
// issue API requests for sections the user never opened.
const tabModules = {
  "tab-tracker": () => import("./tracker.js?v=20260902-tracker-layout-v2"),
  "tab-profile": () => import("./profile.js?v=20260901-error-dialog-minimal-v1"),
  "tab-waterlooworks": () => import("./waterlooworks.js?v=20260902-shared-components-v1"),
  "tab-agent": () => import("./agent.js?v=20260902-memory-alignment-v2"),
  "tab-heatmap": () => import("./heatmap.js?v=20260902-shared-components-v1"),
  "tab-ats-score": () => import("./ats-score.js?v=20260902-shared-ats-v1"),
  "tab-ats-match": () => import("./ats-match.js?v=20260902-shared-ats-v1"),
  "tab-interview": () => import("./interview.js?v=20260902-shared-components-v1"),
  "tab-cover-letter": () => import("./cover-letter.js?v=20260902-shared-components-v1"),
};

const moduleLoads = {};

function ensureTabModule(tabId) {
  if (!tabModules[tabId]) return Promise.resolve(null);
  if (!moduleLoads[tabId]) {
    moduleLoads[tabId] = tabModules[tabId]().catch((err) => {
      delete moduleLoads[tabId];
      console.error(`Module load failed for ${tabId}`, err);
      showSystemFault(err, { title: "Section could not be opened", guidance: "Reload the page and try opening this section again." });
      return null;
    });
  }
  return moduleLoads[tabId];
}

setTabActivators({
  "tab-tracker": () => ensureTabModule("tab-tracker").then((m) => m?.fetchTrackerData()),
  "tab-profile": () => ensureTabModule("tab-profile").then((m) => m?.loadProfileWorkspace()),
  "tab-waterlooworks": () =>
    ensureTabModule("tab-waterlooworks").then((m) => {
      if (!m) return;
      m.startWaterlooWorksPolling();
      m.loadWaterlooWorksStatus();
      m.loadWaterlooWorksJobs();
    }),
  "tab-agent": () =>
    ensureTabModule("tab-agent").then((m) => {
      if (!m) return;
      m.updateContextChip();
      m.renderSessionList();
    }),
  "tab-heatmap": () => ensureTabModule("tab-heatmap").then(module => module?.loadHeatmap()),
  "tab-ats-score": () => ensureTabModule("tab-ats-score"),
  "tab-ats-match": () => ensureTabModule("tab-ats-match"),
  "tab-interview": () => ensureTabModule("tab-interview"),
  "tab-cover-letter": () => ensureTabModule("tab-cover-letter"),
});
initializeNavigation();

document.addEventListener("tracker:data-invalidated", () => {
  if (!moduleLoads["tab-tracker"]) return;
  ensureTabModule("tab-tracker").then((module) => {
    module?.fetchTrackerData({ keepSelection: true });
  });
});

document.addEventListener("tracker:open-requested", (event) => {
  const applicationId = event.detail?.applicationId || null;
  switchTab("tab-tracker");
  ensureTabModule("tab-tracker").then((module) => {
    if (applicationId) module?.openTrackerDrawer(applicationId);
  });
});

// Cross-tab AI action linking from a job detail dialog.
document.addEventListener("click", (event) => {
  const aiBtn = event.target.closest("[data-ai-target]");
  if (aiBtn && jobContextState.activeJobContext) {
    const targetTab = aiBtn.dataset.aiTarget;
    const jd = jobContextState.activeJobContext.jd;

    // Close the originating reader before moving focus into the destination.
    const reader = aiBtn.closest(".job-detail-pane.open");
    reader?.querySelector(".job-detail-pane-close")?.click();
    if (targetTab === "tab-ats-match") {
      const jobDescription = $("#ats-match-jd-text");
      jobDescription.value = jd;
      jobDescription.dispatchEvent(new Event("input", { bubbles: true }));
      $("#ats-match-source").open = true;
      switchTab("tab-ats-match");
      $("#ats-match-resume-text").focus();
    } else if (targetTab === "tab-interview") {
      $("#interview-jd-text").value = jd;
      $("#interview-jd-text").dispatchEvent(new Event("input", { bubbles: true }));
      $("#interview-setup").open = true;
      switchTab("tab-interview");
      $("#interview-jd-text").focus();
    } else if (targetTab === "tab-cover-letter") {
      $("#cl-jd-text").value = jd;
      $("#cl-jd-text").dispatchEvent(new Event("input", { bubbles: true }));
      fillGlassInput($("#cl-job-title"), jobContextState.activeJobContext.title || "");
      fillGlassInput($("#cl-company-name"), jobContextState.activeJobContext.company || "");
      fillGlassInput($("#cl-company-location"), jobContextState.activeJobContext.location || "");
      switchTab("tab-cover-letter");
      $("#cl-brief").open = true;
      $("#cl-jd-text").focus();
    } else if (targetTab === "tab-agent") {
      switchTab("tab-agent");
      import("./agent.js?v=20260902-memory-alignment-v2").then((m) => {
        m.attachActiveJobContext();
        m.updateContextChip();
      });
      $("#agent-input")?.focus();
    }
  }
});

// Initial page load: default tab (Jobs) plus shared infrastructure only.
updateSliderFill();
loadFacets();
loadJobs();
startDesktopJobSyncMonitor({
  refreshJobs: () => Promise.all([loadFacets(), loadJobs()]),
  reportFailure: message => showToast(message, { title: "Could not sync public jobs", variant: "error" }),
});
setupInfiniteScroll({
  rootSelector: "#tab-jobs .results-scroll-content",
  isLoading: () => state.loading,
  canLoadMore: () => state.hasMore && state.cursor,
  loadMore: () => loadJobs({ append: true }),
});
setupBackToTop();
