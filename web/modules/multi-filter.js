import { escapeHtml } from "./helpers.js?v=20260901-error-dialog-minimal-v1";

function resolveMultiFilter(rootOrSelector) {
  return typeof rootOrSelector === "string"
    ? document.querySelector(rootOrSelector)
    : rootOrSelector;
}

function selectedMultiFilterValues(rootOrSelector) {
  const root = resolveMultiFilter(rootOrSelector);
  if (!root) return [];
  return [...root.querySelectorAll('input[type="checkbox"]:checked')]
    .map((input) => input.value);
}

function updateMultiFilterSummary(rootOrSelector) {
  const root = resolveMultiFilter(rootOrSelector);
  if (!root) return;
  const selected = [...root.querySelectorAll('input[type="checkbox"]:checked')];
  const summary = root.querySelector(".multi-filter-summary");
  const clearButton = root.querySelector(".multi-filter-clear");
  if (!summary) return;
  summary.textContent = selected.length === 0
    ? root.dataset.placeholder
    : selected.length === 1
      ? selected[0].dataset.label
      : `${selected.length} selected`;
  if (clearButton) clearButton.hidden = selected.length === 0;
  root.classList.toggle("has-selection", selected.length > 0);
}

function renderMultiFilter(rootOrSelector, items, {
  placeholder,
  getLabel = (item) => item.label || item.value,
  formatCount = (item) => item.count ?? "",
} = {}) {
  const root = resolveMultiFilter(rootOrSelector);
  if (!root) return;
  const id = root.id;
  const selected = new Set(selectedMultiFilterValues(root));
  root.dataset.placeholder = placeholder;
  root.innerHTML = `
    <button class="multi-filter-trigger" type="button" aria-expanded="false">
      <span class="multi-filter-summary">${escapeHtml(placeholder)}</span>
      <span class="multi-filter-chevron" aria-hidden="true"></span>
    </button>
    <div class="multi-filter-menu" hidden>
      <div class="multi-filter-menu-head">
        <span>Select one or more</span>
        <button class="multi-filter-clear" type="button" hidden>Clear</button>
      </div>
      <div class="multi-filter-options">
        ${(items || []).map((item, index) => {
          const display = getLabel(item);
          const count = formatCount(item);
          return `<label class="multi-filter-option" for="${id}-option-${index}">
            <input id="${id}-option-${index}" type="checkbox" value="${escapeHtml(item.value)}" data-label="${escapeHtml(display)}"${selected.has(item.value) ? " checked" : ""} />
            <span>${escapeHtml(display)}</span>
            <small>${escapeHtml(count)}</small>
          </label>`;
        }).join("") || '<p class="multi-filter-empty">No options available</p>'}
      </div>
    </div>`;
  updateMultiFilterSummary(root);
}

function setMultiFilterSelections(rootOrSelector, values) {
  const root = resolveMultiFilter(rootOrSelector);
  if (!root) return;
  const selected = new Set(values);
  root.querySelectorAll('input[type="checkbox"]').forEach((input) => {
    input.checked = selected.has(input.value);
  });
  updateMultiFilterSummary(root);
}

function closeMultiFilters(scope = document, except = null) {
  scope.querySelectorAll(".multi-filter.open").forEach((root) => {
    if (root === except) return;
    root.classList.remove("open");
    root.querySelector(".multi-filter-menu").hidden = true;
    root.querySelector(".multi-filter-trigger").setAttribute("aria-expanded", "false");
  });
}

function toggleMultiFilter(trigger, scope = document) {
  const root = trigger.closest(".multi-filter");
  if (!root) return;
  closeMultiFilters(scope, root);
  const willOpen = !root.classList.contains("open");
  root.classList.toggle("open", willOpen);
  root.querySelector(".multi-filter-menu").hidden = !willOpen;
  trigger.setAttribute("aria-expanded", String(willOpen));
}

function clearMultiFilter(rootOrSelector) {
  const root = resolveMultiFilter(rootOrSelector);
  if (!root) return;
  root.querySelectorAll('input[type="checkbox"]').forEach((input) => {
    input.checked = false;
  });
  updateMultiFilterSummary(root);
}

function setupMultiFilterInteractions(containerOrSelector, {
  scope = document,
  closeOnOutsideClick = false,
  onClear,
  onChange,
} = {}) {
  const container = resolveMultiFilter(containerOrSelector);
  if (!container) return;

  container.addEventListener("click", (event) => {
    const trigger = event.target.closest(".multi-filter-trigger");
    if (trigger) {
      toggleMultiFilter(trigger, scope);
      return;
    }
    const clear = event.target.closest(".multi-filter-clear");
    if (!clear) return;
    const root = clear.closest(".multi-filter");
    clearMultiFilter(root);
    onClear?.(root);
  });

  container.addEventListener("change", (event) => {
    const root = event.target.closest(".multi-filter");
    if (!root) return;
    updateMultiFilterSummary(root);
    onChange?.(root);
  });

  if (closeOnOutsideClick) {
    document.addEventListener("click", (event) => {
      if (!event.target.closest(".multi-filter")) closeMultiFilters(scope);
    });
  }
}

function setMultiFilterSheetOpen(
  sheetOrSelector,
  backdropOrSelector,
  open,
  { closeMenus = false } = {},
) {
  const sheet = resolveMultiFilter(sheetOrSelector);
  const backdrop = resolveMultiFilter(backdropOrSelector);
  if (!sheet || !backdrop) return;
  if (!open && closeMenus) closeMultiFilters(sheet);
  sheet.classList.toggle("open", open);
  sheet.setAttribute("aria-hidden", String(!open));
  backdrop.hidden = !open;
  document.body.classList.toggle("filter-sheet-open", open);
}

export {
  clearMultiFilter,
  closeMultiFilters,
  renderMultiFilter,
  selectedMultiFilterValues,
  setMultiFilterSelections,
  setMultiFilterSheetOpen,
  setupMultiFilterInteractions,
  toggleMultiFilter,
  updateMultiFilterSummary,
};
