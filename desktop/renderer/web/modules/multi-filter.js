import { escapeHtml } from "./helpers.js?v=20260901-error-dialog-minimal-v1";

const lastOptionMarkup = new WeakMap();

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
  if (summary) summary.textContent = selected.length === 0
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
  const options = root.querySelector(".multi-filter-options");
  const entries = (items || []).map((item, index) => {
    const display = getLabel(item);
    const count = formatCount(item);
    return `<label class="multi-filter-option" for="${id}-option-${index}">
      <input id="${id}-option-${index}" type="checkbox" value="${escapeHtml(item.value)}" data-label="${escapeHtml(display)}"${selected.has(item.value) ? " checked" : ""} />
      <span>${escapeHtml(display)}</span>
      <small>${escapeHtml(count)}</small>
    </label>`;
  }).join("");
  // Polling can refresh counts while a category is expanded. Preserve the
  // focused option and the scroll position if the list must be repainted.
  const focused = typeof document !== "undefined" && options.contains?.(document.activeElement)
    ? document.activeElement.value : null;
  const scrollTop = options.scrollTop;
  const markup = entries || '<p class="multi-filter-empty">No options available</p>';
  if (lastOptionMarkup.get(options) !== markup) {
    options.innerHTML = markup;
    lastOptionMarkup.set(options, markup);
    if (focused !== null) {
      Array.from(options.querySelectorAll("input")).find(input => input.value === focused)?.focus({ preventScroll: true });
    }
    options.scrollTop = scrollTop;
  }
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

  container.addEventListener("focusin", event => {
    if (event.target.closest('[role="tablist"]')) closeMultiFilters(container);
  });
  container.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    const open = container.querySelector(".facet-picker.open");
    if (!open) return;
    event.preventDefault();
    event.stopPropagation();
    closeMultiFilters(container);
    open.querySelector(".multi-filter-trigger")?.focus();
  });

  if (closeOnOutsideClick) {
    document.addEventListener("click", (event) => {
      if (!event.target.closest(".multi-filter")) closeMultiFilters(scope);
    });
  }
}

function setupFilterPopover(sheet, trigger) {
  sheet.addEventListener("beforetoggle", (event) => {
    const open = event.newState === "open";
    trigger.setAttribute("aria-expanded", String(open));
    if (!open) {
      closeMultiFilters(sheet);
      return;
    }
    const rect = trigger.getBoundingClientRect();
    sheet.style.setProperty("--filter-top", `${Math.min(rect.bottom + 8, window.innerHeight * 0.3)}px`);
    sheet.style.setProperty("--filter-right", `${window.innerWidth - rect.right}px`);
  });
}

export {
  clearMultiFilter,
  closeMultiFilters,
  renderMultiFilter,
  selectedMultiFilterValues,
  setMultiFilterSelections,
  setupFilterPopover,
  setupMultiFilterInteractions,
  toggleMultiFilter,
  updateMultiFilterSummary,
};
