import { $, showToast } from "./helpers.js?v=20260901-error-dialog-minimal-v1";

const requests = new WeakMap();

async function openJobDetailPane({
  paneSelector,
  detailSelector,
  cardsSelector,
  selectedId,
  getCardId,
  loadDetail,
  errorTitle,
  focus = true,
}) {
  const pane = $(paneSelector);
  const detail = $(detailSelector);
  if (!pane || !detail) return null;

  requests.get(pane)?.abort();
  const request = new AbortController();
  requests.set(pane, request);
  detail.innerHTML = '<p class="loading-detail" role="status">Loading job details…</p>';
  pane.classList.add("open", "has-selection");
  pane.setAttribute("aria-hidden", "false");
  pane.setAttribute("data-selected-id", String(selectedId));
  pane.scrollTop = 0;
  document.querySelectorAll(cardsSelector).forEach((card) => {
    const selected = getCardId(card) === String(selectedId);
    card.classList.toggle("selected", selected);
    card.querySelector?.(".job-title-button")?.setAttribute("aria-expanded", String(selected));
  });
  if (focus) pane.querySelector?.(".job-detail-pane-close")?.focus({ preventScroll: true });

  try {
    const result = await loadDetail(selectedId, request.signal);
    if (request.signal.aborted) return null;
    detail.innerHTML = result.html;
    return result;
  } catch (error) {
    if (request.signal.aborted) return null;
    closeJobDetailPane({ paneSelector, selectedCardsSelector: cardsSelector + ".selected", restoreFocus: focus });
    showToast(errorTitle, { variant: "error" });
    return null;
  }
}

function closeJobDetailPane({ paneSelector, selectedCardsSelector, restoreFocus = true }) {
  const pane = $(paneSelector);
  requests.get(pane)?.abort();
  pane?.classList.remove("open", "has-selection");
  pane?.setAttribute("aria-hidden", "true");
  document.querySelectorAll(selectedCardsSelector).forEach((card) => {
    card.classList.remove("selected");
    const title = card.querySelector?.(".job-title-button");
    title?.setAttribute("aria-expanded", "false");
    if (restoreFocus) title?.focus({ preventScroll: true });
  });
}

function setupJobSplit(divider) {
  if (!divider) return;
  const workspace = divider.parentElement;
  const min = Number(divider.getAttribute("aria-valuemin"));
  const max = Number(divider.getAttribute("aria-valuemax"));
  function resize(percent) {
    const value = Math.round(Math.min(max, Math.max(min, percent)));
    workspace.style.setProperty("--list-width", value + "%");
    divider.setAttribute("aria-valuenow", String(value));
  }
  divider.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const value = Number(divider.getAttribute("aria-valuenow"));
    if (event.key === "Home") resize(min);
    else if (event.key === "End") resize(max);
    else resize(value + (event.key === "ArrowRight" ? 2 : -2));
  });
  divider.addEventListener("pointerdown", (event) => {
    if (event.button === 0) divider.setPointerCapture(event.pointerId);
  });
  divider.addEventListener("pointermove", (event) => {
    if (!divider.hasPointerCapture(event.pointerId)) return;
    const rect = workspace.getBoundingClientRect();
    resize(100 * (event.clientX - rect.left) / rect.width);
  });
}

export { closeJobDetailPane, openJobDetailPane, setupJobSplit };
