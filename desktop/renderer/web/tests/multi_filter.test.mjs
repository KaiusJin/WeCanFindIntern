import assert from "node:assert/strict";
import test from "node:test";

import {
  clearMultiFilter,
  renderMultiFilter,
  selectedMultiFilterValues,
  setMultiFilterSelections,
  setupFilterPopover,
  setupMultiFilterInteractions,
  toggleMultiFilter,
} from "../modules/multi-filter.js?v=20260902-shared-filters-v1";

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
    optionsPane: { innerHTML: "", dataset: {} },
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
      if (selector === ".multi-filter-options") return this.optionsPane;
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

test("facet options retain checked values while the React trigger stays mounted", () => {
  const root = fakeFilter("work-mode", [fakeOption("remote", "Remote", true)]);

  renderMultiFilter(root, [
    { value: "remote", count: 12 },
    { value: "hybrid", count: 8 },
  ], {
    placeholder: "All work modes",
    getLabel: (item) => item.value === "remote" ? "Remote" : "Hybrid",
    formatCount: (item) => item.count.toLocaleString(),
  });

  assert.doesNotMatch(root.optionsPane.innerHTML, /multi-filter-trigger|multi-filter-menu/);
  assert.match(root.optionsPane.innerHTML, /id="work-mode-option-0"[^>]*value="remote"[^>]*checked/);
  assert.match(root.optionsPane.innerHTML, /<span>Remote<\/span>\s*<small>12<\/small>/);
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

test("the native filter popover positions at its trigger and updates the expanded state on light dismiss", () => {
  let beforeToggle;
  const styles = new Map();
  const sheet = {
    addEventListener(type, callback) { if (type === "beforetoggle") beforeToggle = callback; },
    querySelectorAll: () => [],
    style: { setProperty: (key, value) => styles.set(key, value) },
  };
  const trigger = {
    setAttribute(name, value) { this[name] = value; },
    getBoundingClientRect: () => ({ bottom: 65, right: 400 }),
  };
  globalThis.window = { innerWidth: 1200, innerHeight: 800 };
  setupFilterPopover(sheet, trigger);
  beforeToggle({ newState: "open" });
  assert.equal(trigger["aria-expanded"], "true");
  assert.equal(styles.get("--filter-right"), "800px");
  assert.equal(styles.get("--filter-top"), "73px");
  beforeToggle({ newState: "closed" });
  assert.equal(trigger["aria-expanded"], "false");
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
    scope: container,
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
