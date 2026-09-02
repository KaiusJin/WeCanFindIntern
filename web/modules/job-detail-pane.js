import { $, showErrorDialog } from "./helpers.js?v=20260901-error-dialog-minimal-v1";

async function openJobDetailPane({
  paneSelector,
  detailSelector,
  cardsSelector,
  selectedId,
  getCardId,
  loadDetail,
  errorTitle,
}) {
  const pane = $(paneSelector);
  const detail = $(detailSelector);
  if (!pane || !detail) return null;

  detail.innerHTML = '<p class="loading-detail">Loading job details…</p>';
  pane.classList.add("open", "has-selection");
  pane.setAttribute("aria-hidden", "false");
  document.querySelectorAll(cardsSelector).forEach((card) => {
    card.classList.toggle("selected", getCardId(card) === String(selectedId));
  });

  try {
    const result = await loadDetail(selectedId);
    detail.innerHTML = result.html;
    return result;
  } catch (error) {
    pane.classList.remove("open", "has-selection");
    pane.setAttribute("aria-hidden", "true");
    showErrorDialog(error, { title: errorTitle });
    return null;
  }
}

function closeJobDetailPane({ paneSelector, selectedCardsSelector }) {
  const pane = $(paneSelector);
  pane?.classList.remove("open");
  pane?.setAttribute("aria-hidden", "true");
  document.querySelectorAll(selectedCardsSelector).forEach((card) => {
    card.classList.remove("selected");
  });
}

export { closeJobDetailPane, openJobDetailPane };
