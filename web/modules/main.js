import { $, showErrorDialog } from "./helpers.js";
import { loadSettings } from "./settings.js";
import { setTabActivators, switchTab } from "./navigation.js";
import {
  state,
  loadJobs,
  loadFacets,
  updateSliderFill,
  setupInfiniteScroll,
  setupBackToTop,
} from "./jobs.js";

// Settings must be applied before any AI feature runs.
loadSettings();

// Non-default tabs load their modules lazily: switching activates the tab
// (which refreshes its data), and hovering a nav tab preloads the module so
// the click lands on an already-parsed graph.
const tabModules = {
  "tab-tracker": () => import("./tracker.js"),
  "tab-profile": () => import("./profile.js"),
  "tab-waterlooworks": () => import("./waterlooworks.js?v=20260828-fix-v26"),
  "tab-agent": () => import("./agent.js"),
  "tab-heatmap": () => import("./heatmap.js"),
  "tab-ats": () => import("./ats.js"),
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
      m.loadWaterlooWorksStatus();
      m.loadWaterlooWorksJobs();
    }),
  "tab-agent": () =>
    ensureTabModule("tab-agent").then((m) => {
      m.updateContextChip();
      m.renderSessionList();
    }),
  "tab-heatmap": () => ensureTabModule("tab-heatmap"),
  "tab-ats": () => ensureTabModule("tab-ats"),
  "tab-interview": () => ensureTabModule("tab-interview"),
  "tab-cover-letter": () => ensureTabModule("tab-cover-letter"),
});

document.querySelectorAll(".nav-tab").forEach((btn) => {
  btn.addEventListener("mouseenter", () => ensureTabModule(btn.dataset.tab));
});

// Cross-tab AI action linking from a job detail dialog.
document.addEventListener("click", (event) => {
  const aiBtn = event.target.closest("[data-ai-target]");
  if (aiBtn && state.activeJobContext) {
    const targetTab = aiBtn.dataset.aiTarget;
    const jd = state.activeJobContext.jd;

    if (targetTab === "tab-ats") {
      $("#ats-jd-text").value = jd;
      switchTab("tab-ats");
      $("#ats-resume-text").focus();
    } else if (targetTab === "tab-interview") {
      $("#interview-jd-text").value = jd;
      switchTab("tab-interview");
      $("#btn-generate-questions")?.scrollIntoView({ behavior: "smooth" });
    } else if (targetTab === "tab-cover-letter") {
      $("#cl-jd-text").value = jd;
      switchTab("tab-cover-letter");
      $("#cl-resume-text").focus();
    } else if (targetTab === "tab-agent") {
      switchTab("tab-agent");
      import("./agent.js").then((m) => {
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
setupInfiniteScroll();
setupBackToTop();
