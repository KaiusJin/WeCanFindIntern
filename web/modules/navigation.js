const tabActivators = {};

const tabHashes = {
  "tab-jobs": "public-jobs",
  "tab-heatmap": "public-jobs/map",
  "tab-waterlooworks": "waterlooworks",
  "tab-tracker": "applications",
  "tab-ats-score": "resume-ats",
  "tab-ats-match": "job-match",
  "tab-cover-letter": "cover-letter",
  "tab-interview": "interview-coach",
  "tab-agent": "assistant",
  "tab-profile": "profile",
};

const hashTabs = Object.fromEntries(Object.entries(tabHashes).map(([tab, hash]) => [hash, tab]));

export function setTabActivators(activators) {
  Object.assign(tabActivators, activators);
}

export function initializeNavigation() {
  const targetTab = hashTabs[window.location.hash.slice(1)];
  if (targetTab) switchTab(targetTab, { updateLocation: false });
  else syncPublicJobsView("tab-jobs");
}

function navTabFor(targetTabId) {
  return targetTabId === "tab-heatmap" ? "tab-jobs" : targetTabId;
}

function syncPublicJobsView(targetTabId) {
  const activeView = targetTabId === "tab-heatmap" ? "map" : "list";
  document.querySelectorAll("[data-public-jobs-view]").forEach((button) => {
    const active = button.dataset.publicJobsView === activeView;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function closeSidebar() {
  document.body.classList.remove("sidebar-open");
  const backdrop = document.querySelector("#app-sidebar-backdrop");
  if (backdrop) backdrop.hidden = true;
}

function openSidebar() {
  document.body.classList.add("sidebar-open");
  const backdrop = document.querySelector("#app-sidebar-backdrop");
  if (backdrop) backdrop.hidden = false;
}

function closeAgentDialogsWhenLeaving(targetTabId) {
  const activeTabId = document.querySelector(".tab-pane.active:not([hidden])")?.id;
  if (activeTabId !== "tab-agent" || targetTabId === "tab-agent") return;

  document.querySelectorAll("#agent-attach-dialog[open], #agent-delete-session-dialog[open]")
    .forEach((dialog) => dialog.close());
  if (!document.querySelector("dialog[open]")) {
    document.body.classList.remove("modal-open");
    document.documentElement.classList.remove("modal-open");
  }
}

export function switchTab(targetTabId, { updateLocation = true } = {}) {
  closeAgentDialogsWhenLeaving(targetTabId);
  const navTarget = navTabFor(targetTabId);
  document.querySelectorAll(".nav-tab").forEach((button) => {
    const active = button.dataset.tab === navTarget;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });

  document.querySelectorAll(".tab-pane").forEach((pane) => {
    const active = pane.id === targetTabId;
    pane.hidden = !active;
    pane.classList.toggle("active", active);
  });

  syncPublicJobsView(targetTabId);
  tabActivators[targetTabId]?.();

  const appMain = document.querySelector("#app-main");
  if (appMain) appMain.scrollTo({ top: 0, behavior: "instant" });
  else window.scrollTo({ top: 0, behavior: "instant" });

  if (updateLocation) {
    const nextHash = tabHashes[targetTabId];
    if (nextHash && window.location.hash.slice(1) !== nextHash) {
      window.history.replaceState(null, "", `#${nextHash}`);
    }
  }
  closeSidebar();
}

document.querySelectorAll(".nav-tab").forEach((button) => {
  button.addEventListener("click", () => switchTab(button.dataset.tab));
});

document.querySelectorAll("[data-public-jobs-view]").forEach((button) => {
  button.addEventListener("click", () => {
    switchTab(button.dataset.publicJobsView === "map" ? "tab-heatmap" : "tab-jobs");
  });
});

document.querySelector("#open-app-sidebar")?.addEventListener("click", openSidebar);
document.querySelector("#close-app-sidebar")?.addEventListener("click", closeSidebar);
document.querySelector("#app-sidebar-backdrop")?.addEventListener("click", closeSidebar);
document.querySelector("#sidebar-open-settings")?.addEventListener("click", () => {
  closeSidebar();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && document.body.classList.contains("sidebar-open")) closeSidebar();
});

window.addEventListener("hashchange", () => {
  const targetTab = hashTabs[window.location.hash.slice(1)];
  if (targetTab) switchTab(targetTab, { updateLocation: false });
});
