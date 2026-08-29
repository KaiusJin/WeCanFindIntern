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
import { fetchTrackerData } from "./tracker.js";
import { loadProfileWorkspace } from "./profile.js";
// Cache-busted so browsers re-fetch the module after the formatDate import fix.
import { loadWaterlooWorksStatus, loadWaterlooWorksJobs } from "./waterlooworks.js?v=20260828-fix-v26";
import { updateContextChip, renderSessionList } from "./agent.js";
import "./ats.js";
import "./interview.js";
import "./cover-letter.js";

// Settings must be applied before any AI feature runs.
loadSettings();

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
      $("#agent-input")?.focus();
    }
    $("#job-dialog")?.close();
  }
});

// Tab activation callbacks (wired here to avoid circular imports).
setTabActivators({
  "tab-tracker": () => fetchTrackerData(),
  "tab-profile": () => loadProfileWorkspace(),
  "tab-waterlooworks": () => {
    loadWaterlooWorksStatus();
    loadWaterlooWorksJobs();
  },
  "tab-agent": () => {
    updateContextChip();
    renderSessionList();
  },
});

// Initial page load.
updateSliderFill();
loadFacets();
loadJobs();
fetchTrackerData();
setupInfiniteScroll();
setupBackToTop();
