import { renderJobCard } from "./helpers.js?v=20260901-error-dialog-minimal-v1";
import { bookmarkState, updateBookmarkButtons } from "./bookmarks.js";
import { closeJobDetailPane, openJobDetailPane, setupJobSplit } from "./job-detail-pane.js?v=20260902-shared-components-v1";

export function setupPostingWorkspace(tab, { waterlooWorks = false, loadDetail, activateJob }) {
  const list = tab.querySelector(".job-list");
  const pane = tab.querySelector(".job-detail-pane");
  const detail = pane.querySelector(".job-detail-content");
  const scrollRoot = tab.querySelector(".results-scroll-content");
  const topButton = tab.querySelector(".posting-to-top");
  const empty = tab.querySelector(".empty-state");
  const cardsSelector = "#" + list.id + " .job-card";
  const paneSelector = "#" + pane.id;
  let readerCollapsed = false;
  let visibleJob;

  async function openDetail(id, focus = true) {
    if (focus) readerCollapsed = false;
    const result = await openJobDetailPane({
      paneSelector, detailSelector: "#" + detail.id, cardsSelector, selectedId: id,
      getCardId: card => card.dataset.id, loadDetail, focus,
      errorTitle: waterlooWorks ? "WaterlooWorks job details unavailable" : "Job details unavailable",
    });
    if (result) {
      visibleJob = result.job;
      if (!tab.hidden) activateJob(visibleJob);
      updateBookmarkButtons();
    }
  }

  function closeDetail(userInitiated = true) {
    if (userInitiated) readerCollapsed = true;
    visibleJob = null;
    closeJobDetailPane({ paneSelector, selectedCardsSelector: cardsSelector + ".selected", restoreFocus: userInitiated });
  }

  function previewFirstResult() {
    if (readerCollapsed || tab.hidden || pane.classList.contains("open") || window.matchMedia("(max-width: 680px)").matches) return;
    const first = list.querySelector(".job-card");
    if (first) void openDetail(first.dataset.id, false);
  }

  function renderRows(items, append) {
    const selectedId = pane.classList.contains("open") ? pane.dataset.selectedId : null;
    const seen = new Set(append ? Array.from(list.children, card => card.dataset.id) : []);
    const saved = waterlooWorks ? bookmarkState.waterlooWorksJobs : bookmarkState.publicJobs;
    const rows = [];
    for (const job of items) {
      const id = waterlooWorks ? job.source_job_id : job.id;
      if (seen.has(id)) continue;
      seen.add(id);
      rows.push(renderJobCard(job, { waterlooWorks, selected: id === selectedId, isSaved: saved.has(id) }));
    }
    if (!append) list.replaceChildren();
    list.insertAdjacentHTML("beforeend", rows.join(""));
    updateBookmarkButtons();
    if (!append) {
      if (selectedId && !seen.has(selectedId)) closeDetail(false);
      previewFirstResult();
    }
    return rows.length;
  }

  function showEmpty(title, message, actionLabel = "", onAction = null) {
    empty.querySelector("h3").textContent = title;
    empty.querySelector("p").textContent = message;
    const button = empty.querySelector("button");
    (button.querySelector(".lg-button__label") || button).textContent = actionLabel;
    button.onclick = onAction;
    button.hidden = !onAction;
    empty.hidden = false;
  }

  list.addEventListener("click", event => {
    const card = event.target.closest(".job-card");
    if (card && !event.target.closest("button:not(.job-title-button), a")) void openDetail(card.dataset.id);
  });
  detail.addEventListener("click", event => {
    if (visibleJob && event.target.closest("[data-ai-target]")) activateJob(visibleJob);
  });
  pane.querySelector(".job-detail-pane-close").addEventListener("click", () => closeDetail());
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape" || event.defaultPrevented || tab.hidden || !pane.classList.contains("open")) return;
    if (document.querySelector('dialog:modal, [popover="auto"]:popover-open') || detail.querySelector("posting-menu [aria-expanded=true]")) return;
    const menu = detail.querySelector("details[open]");
    if (menu) {
      menu.open = false;
      menu.querySelector("summary").focus();
    } else closeDetail();
  });
  scrollRoot.addEventListener("scroll", () => { topButton.hidden = scrollRoot.scrollTop < 500; }, { passive: true });
  topButton.addEventListener("click", () => scrollRoot.scrollTo({ top: 0, behavior: "smooth" }));
  setupJobSplit(tab.querySelector(".discovery-divider"));
  new ResizeObserver(previewFirstResult).observe(tab);
  return { closeDetail, renderRows, showEmpty };
}
