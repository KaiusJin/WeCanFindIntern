import { $ } from "./helpers.js";

const tabActivators = {};

export function setTabActivators(activators) {
  Object.assign(tabActivators, activators);
}

export function switchTab(targetTabId) {
  document.querySelectorAll(".nav-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === targetTabId);
  });
  document.querySelectorAll(".tab-pane").forEach((pane) => {
    if (pane.id === targetTabId) {
      pane.hidden = false;
      pane.classList.add("active");
    } else {
      pane.hidden = true;
      pane.classList.remove("active");
    }
  });
  tabActivators[targetTabId]?.();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

document.querySelectorAll(".nav-tab").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});
