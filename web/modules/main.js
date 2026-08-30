import { $ } from "./helpers.js";
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

// Non-default tabs load their modules on first activation: switching to a tab
// fires its activator (see navigation.js), which dynamically imports the
// module graph for that feature. The initial page load only pulls the Jobs
// tab plus shared helpers.
setTabActivators({
  "tab-tracker": () => import("./tracker.js").then((m) => m.fetchTrackerData()),
  "tab-profile": () => import("./profile.js").then((m) => m.loadProfileWorkspace()),
  "tab-waterlooworks": () =>
    import("./waterlooworks.js?v=20260828-fix-v26").then((m) => {
      m.loadWaterlooWorksStatus();
      m.loadWaterlooWorksJobs();
    }),
  "tab-agent": () =>
    import("./agent.js").then((m) => {
      m.updateContextChip();
      m.renderSessionList();
    }),
  "tab-heatmap": () => import("./heatmap.js"),
  "tab-ats": () => import("./ats.js"),
  "tab-interview": () => import("./interview.js"),
  "tab-cover-letter": () => import("./cover-letter.js"),
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
