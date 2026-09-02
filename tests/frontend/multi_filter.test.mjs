import assert from "node:assert/strict";
import test from "node:test";

import {
  clearMultiFilter,
  renderMultiFilter,
  selectedMultiFilterValues,
  setMultiFilterSelections,
  setMultiFilterSheetOpen,
  setupMultiFilterInteractions,
  toggleMultiFilter,
} from "../../web/modules/multi-filter.js?v=20260902-shared-filters-v1";

class FakeClassList {
  constructor(...names) {
    this.names = new Set(names);
  }

  contains(name) {
    return this.names.has(name);
  }

  remove(name) {
    this.names.delete(name);
  }

  toggle(name, force) {
    const enabled = force === undefined ? !this.names.has(name) : force;
    if (enabled) this.names.add(name);
    else this.names.delete(name);
    return enabled;
  }
}

function fakeFilter(id, options = []) {
  const summary = { textContent: "" };
  const clear = { hidden: true };
  const menu = { hidden: true };
  const trigger = {
    ariaExpanded: "false",
    setAttribute(name, value) {
      if (name === "aria-expanded") this.ariaExpanded = value;
    },
  };
  return {
    id,
    options,
    dataset: {},
    classList: new FakeClassList(),
    innerHTML: "",
    summary,
    clear,
    menu,
    trigger,
    querySelectorAll(selector) {
      if (selector === 'input[type="checkbox"]:checked') {
        return this.options.filter((option) => option.checked);
      }
      if (selector === 'input[type="checkbox"]') return this.options;
      return [];
    },
    querySelector(selector) {
      if (selector === ".multi-filter-summary") return summary;
      if (selector === ".multi-filter-clear") return clear;
      if (selector === ".multi-filter-menu") return menu;
      if (selector === ".multi-filter-trigger") return trigger;
      return null;
    },
  };
}

function fakeOption(value, label, checked = false) {
  return { value, checked, dataset: { label } };
}

test("shared multi-filter keeps the existing markup and selected values", () => {
  const root = fakeFilter("work-mode", [fakeOption("remote", "Remote", true)]);

  renderMultiFilter(root, [
    { value: "remote", count: 12 },
    { value: "hybrid", count: 8 },
  ], {
    placeholder: "All work modes",
    getLabel: (item) => item.value === "remote" ? "Remote" : "Hybrid",
    formatCount: (item) => item.count.toLocaleString(),
  });

  assert.match(root.innerHTML, /class="multi-filter-trigger"/);
  assert.match(root.innerHTML, /class="multi-filter-menu" hidden/);
  assert.match(root.innerHTML, /id="work-mode-option-0"[^>]*value="remote"[^>]*checked/);
  assert.match(root.innerHTML, /<span>Remote<\/span>\s*<small>12<\/small>/);
  assert.equal(root.dataset.placeholder, "All work modes");
  assert.equal(root.summary.textContent, "Remote");
  assert.equal(root.clear.hidden, false);
  assert.equal(root.classList.contains("has-selection"), true);
});

test("shared multi-filter updates and clears selections", () => {
  const root = fakeFilter("board", [
    fakeOption("all", "All boards", true),
    fakeOption("applications", "Submitted applications"),
  ]);
  root.dataset.placeholder = "All board sources";

  setMultiFilterSelections(root, ["applications"]);
  assert.deepEqual(selectedMultiFilterValues(root), ["applications"]);
  assert.equal(root.summary.textContent, "Submitted applications");

  clearMultiFilter(root);
  assert.deepEqual(selectedMultiFilterValues(root), []);
  assert.equal(root.summary.textContent, "All board sources");
  assert.equal(root.clear.hidden, true);
});

test("shared multi-filter closes sibling menus before opening the target", () => {
  const root = fakeFilter("target");
  const sibling = fakeFilter("sibling");
  sibling.classList.toggle("open", true);
  sibling.menu.hidden = false;
  sibling.trigger.ariaExpanded = "true";
  const trigger = {
    ariaExpanded: "false",
    closest: () => root,
    setAttribute(name, value) {
      if (name === "aria-expanded") this.ariaExpanded = value;
    },
  };
  const scope = {
    querySelectorAll: (selector) => selector === ".multi-filter.open" ? [sibling] : [],
  };

  toggleMultiFilter(trigger, scope);

  assert.equal(root.classList.contains("open"), true);
  assert.equal(root.menu.hidden, false);
  assert.equal(trigger.ariaExpanded, "true");
  assert.equal(sibling.classList.contains("open"), false);
  assert.equal(sibling.menu.hidden, true);
  assert.equal(sibling.trigger.ariaExpanded, "false");
});

test("shared filter sheet preserves visibility, aria state, and body scroll state", () => {
  const sheet = fakeFilter("sheet");
  const backdrop = { hidden: true };
  const bodyClasses = new FakeClassList();
  globalThis.document = { body: { classList: bodyClasses } };
  sheet.ariaHidden = "true";
  sheet.setAttribute = (name, value) => {
    if (name === "aria-hidden") sheet.ariaHidden = value;
  };

  setMultiFilterSheetOpen(sheet, backdrop, true);
  assert.equal(sheet.classList.contains("open"), true);
  assert.equal(sheet.ariaHidden, "false");
  assert.equal(backdrop.hidden, false);
  assert.equal(bodyClasses.contains("filter-sheet-open"), true);

  setMultiFilterSheetOpen(sheet, backdrop, false);
  assert.equal(sheet.classList.contains("open"), false);
  assert.equal(sheet.ariaHidden, "true");
  assert.equal(backdrop.hidden, true);
  assert.equal(bodyClasses.contains("filter-sheet-open"), false);
});

test("shared multi-filter interactions delegate clear and change behavior", () => {
  const listeners = new Map();
  const container = {
    addEventListener(type, listener) {
      listeners.set(type, listener);
    },
  };
  const root = fakeFilter("delegated", [fakeOption("remote", "Remote", true)]);
  root.dataset.placeholder = "All work modes";
  let clears = 0;
  let changes = 0;

  setupMultiFilterInteractions(container, {
    onClear: () => { clears += 1; },
    onChange: () => { changes += 1; },
  });

  const clearTarget = {
    closest(selector) {
      if (selector === ".multi-filter-clear") return this;
      if (selector === ".multi-filter") return root;
      return null;
    },
  };
  listeners.get("click")({ target: clearTarget });
  assert.deepEqual(selectedMultiFilterValues(root), []);
  assert.equal(clears, 1);

  root.options[0].checked = true;
  listeners.get("change")({ target: { closest: () => root } });
  assert.equal(root.summary.textContent, "Remote");
  assert.equal(changes, 1);
});
