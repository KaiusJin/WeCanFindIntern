import { $, showErrorDialog } from "./helpers.js";
import { loadSettings } from "./settings.js";
import { setTabActivators, switchTab } from "./navigation.js";
import { jobContextState } from "./job-context.js?v=20260831-jobboard-parity-v3";
import { setupInfiniteScroll } from "./pagination.js?v=20260831-jobboard-parity-v3";
import { startDesktopCollectionMonitor } from "./desktop-collection.js?v=20260901-job-sync-v1";
import {
  state,
  loadJobs,
  loadFacets,
  updateSliderFill,
  setupBackToTop,
} from "./jobs.js?v=20260831-jobboard-parity-v3";

// Settings must be applied before any AI feature runs.
await loadSettings();

// Non-default tabs load only when activated. Several modules initialize their
// section state at import time, so speculative hover imports would otherwise
// issue API requests for sections the user never opened.
const tabModules = {
  "tab-tracker": () => import("./tracker.js"),
  "tab-profile": () => import("./profile.js"),
  "tab-waterlooworks": () => import("./waterlooworks.js?v=20260831-jobboard-parity-v3"),
  "tab-agent": () => import("./agent.js?v=20260901-approval-result-v1"),
  "tab-heatmap": () => import("./heatmap.js"),
  "tab-ats-score": () => import("./ats-score.js?v=20260901-ai-commentary-v2"),
  "tab-ats-match": () => import("./ats-match.js?v=20260901-ai-commentary-v1"),
  "tab-interview": () => import("./interview.js"),
  "tab-cover-letter": () => import("./cover-letter.js"),
};

const moduleLoads = {};

function ensureTabModule(tabId) {
  if (!tabModules[tabId]) return Promise.resolve(null);
  if (!moduleLoads[tabId]) {
    moduleLoads[tabId] = tabModules[tabId]().catch((err) => {
      delete moduleLoads[tabId];
      console.error(`Module load failed for ${tabId}`, err);
      showErrorDialog(err, { title: "Section could not be opened", guidance: "Reload the page and try opening this section again." });
      throw err;
    });
  }
  return moduleLoads[tabId];
}

setTabActivators({
  "tab-tracker": () => ensureTabModule("tab-tracker").then((m) => m.fetchTrackerData()),
  "tab-profile": () => ensureTabModule("tab-profile").then((m) => m.loadProfileWorkspace()),
  "tab-waterlooworks": () =>
    ensureTabModule("tab-waterlooworks").then((m) => {
      m.startWaterlooWorksPolling();
      m.loadWaterlooWorksStatus();
      m.loadWaterlooWorksJobs();
    }),
  "tab-agent": () =>
    ensureTabModule("tab-agent").then((m) => {
      m.updateContextChip();
      m.renderSessionList();
    }),
  "tab-heatmap": () => ensureTabModule("tab-heatmap"),
  "tab-ats-score": () => ensureTabModule("tab-ats-score"),
  "tab-ats-match": () => ensureTabModule("tab-ats-match"),
  "tab-interview": () => ensureTabModule("tab-interview"),
  "tab-cover-letter": () => ensureTabModule("tab-cover-letter"),
});

document.addEventListener("tracker:data-invalidated", () => {
  if (!moduleLoads["tab-tracker"]) return;
  ensureTabModule("tab-tracker").then((module) => {
    module.fetchTrackerData({ keepSelection: true });
  });
});

document.addEventListener("tracker:open-requested", (event) => {
  const applicationId = event.detail?.applicationId || null;
  switchTab("tab-tracker");
  ensureTabModule("tab-tracker").then((module) => {
    if (applicationId) module.openTrackerDrawer(applicationId);
  });
});

// Cross-tab AI action linking from a job detail dialog.
document.addEventListener("click", (event) => {
  const aiBtn = event.target.closest("[data-ai-target]");
  if (aiBtn && jobContextState.activeJobContext) {
    const targetTab = aiBtn.dataset.aiTarget;
    const jd = jobContextState.activeJobContext.jd;

    if (targetTab === "tab-ats-match") {
      const jobDescription = $("#ats-match-jd-text");
      jobDescription.value = jd;
      jobDescription.dispatchEvent(new Event("input", { bubbles: true }));
      switchTab("tab-ats-match");
      $("#ats-match-resume-text").focus();
    } else if (targetTab === "tab-interview") {
      $("#interview-jd-text").value = jd;
      switchTab("tab-interview");
      $("#btn-generate-questions")?.scrollIntoView({ behavior: "smooth" });
    } else if (targetTab === "tab-cover-letter") {
      $("#cl-jd-text").value = jd;
      $("#cl-job-title").value = jobContextState.activeJobContext.title || "";
      $("#cl-company-name").value = jobContextState.activeJobContext.company || "";
      $("#cl-company-location").value = jobContextState.activeJobContext.location || "";
      switchTab("tab-cover-letter");
      $("#cl-resume-text").focus();
    } else if (targetTab === "tab-agent") {
      switchTab("tab-agent");
      import("./agent.js?v=20260901-approval-result-v1").then((m) => {
        m.attachActiveJobContext();
        m.updateContextChip();
      });
      $("#agent-input")?.focus();
    }
    $("#job-dialog")?.close();
  }
});

// Initial page load: default tab (Jobs) plus shared infrastructure only.
updateSliderFill();
loadFacets();
loadJobs();
startDesktopCollectionMonitor({
  refreshJobs: () => Promise.all([loadFacets(), loadJobs()]),
});
setupInfiniteScroll({
  isLoading: () => state.loading,
  canLoadMore: () => state.hasMore && state.cursor,
  loadMore: () => loadJobs({ append: true }),
});
setupBackToTop();
