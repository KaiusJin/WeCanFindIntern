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
  "tab-settings": "settings",
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
  if (targetTabId !== "tab-jobs" && targetTabId !== "tab-heatmap") return;
  const view = targetTabId === "tab-heatmap" ? "map" : "list";
  const host = document.querySelector(`[data-public-jobs-view="${view}"]`);
  const control = document.getElementById("public-view-control");
  if (control.parentElement !== host) host.append(control);
  document.dispatchEvent(new Event("wcfi:public-view-changed"));
}

const toolbarHost = document.querySelector("#toolbar-nav-host");
const nav = toolbarHost.querySelector(".app-nav");
const workspacePicker = document.getElementById("workspace-picker");
workspacePicker.addEventListener("change", () => switchTab(workspacePicker.value));

function dismissGroups(except = null) {
  nav.querySelectorAll('.group-toggle[aria-expanded="true"]').forEach(button => {
    if (button !== except) button.setAttribute("aria-expanded", "false");
  });
}

nav.addEventListener("click", event => {
  const button = event.target.closest(".group-toggle");
  if (!button) return;
  const open = button.getAttribute("aria-expanded") === "true";
  dismissGroups(button);
  button.setAttribute("aria-expanded", String(!open));
});

document.addEventListener("pointerdown", event => {
  if (!toolbarHost.contains(event.target)) dismissGroups();
});
nav.addEventListener("focusout", event => {
  const group = event.target.closest(".nav-group");
  if (event.relatedTarget && group && !group.contains(event.relatedTarget)) {
    group.querySelector(".group-toggle")?.setAttribute("aria-expanded", "false");
  }
});
nav.addEventListener("keydown", event => {
  const group = event.target.closest(".nav-group");
  const button = group?.querySelector(".group-toggle");
  if (!button) return;
  if (event.key === "Escape") {
    dismissGroups();
    button.focus();
  } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    dismissGroups(button);
    button.setAttribute("aria-expanded", "true");
    const items = Array.from(group.querySelectorAll(".nav-tab"));
    const current = items.indexOf(document.activeElement);
    const next = current === -1 && event.key === "ArrowUp" ? items.length - 1
      : (current + (event.key === "ArrowUp" ? -1 : 1) + items.length) % items.length;
    items[next].focus();
  }
});

function closeAgentDialogsWhenLeaving(targetTabId) {
  const activeTabId = document.querySelector(".tab-pane.active:not([hidden])")?.id;
  if (activeTabId !== "tab-agent" || targetTabId === "tab-agent") return;

  document.dispatchEvent(new Event("assistant:leave"));
}

export function switchTab(targetTabId, { updateLocation = true } = {}) {
  if (targetTabId !== "tab-settings" && !document.getElementById("tab-settings").hidden) {
    document.getElementById("tab-settings").dispatchEvent(new Event("settings:leave"));
  }
  closeAgentDialogsWhenLeaving(targetTabId);
  const navTarget = navTabFor(targetTabId);
  workspacePicker.value = navTarget;
  for (const panel of document.querySelectorAll("[data-workspace-popover]:popover-open")) {
    if (panel.dataset.workspacePopover !== targetTabId) panel.hidePopover();
  }
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

  const current = nav.querySelector(".nav-tab.active");
  if (toolbarHost.contains(document.activeElement) && document.activeElement.matches(".nav-tab")) {
    current?.closest(".nav-group")?.querySelector(".group-toggle")?.focus();
  }
  dismissGroups();
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
}

document.querySelectorAll(".nav-tab").forEach((button) => {
  button.addEventListener("click", () => switchTab(button.dataset.tab));
});

document.addEventListener("wcfi:request-public-view", ({ detail }) => {
  if (detail !== "list" && detail !== "map") return;
  switchTab(detail === "map" ? "tab-heatmap" : "tab-jobs");
});

document.addEventListener("settings:navigate", ({ detail }) => {
  if (Object.hasOwn(tabHashes, detail)) switchTab(detail);
});

window.addEventListener("hashchange", () => {
  const targetTab = hashTabs[window.location.hash.slice(1)];
  if (targetTab) switchTab(targetTab, { updateLocation: false });
});
