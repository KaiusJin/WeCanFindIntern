import assert from "node:assert/strict";
import test from "node:test";

class FakeClassList {
  constructor() {
    this.names = new Set();
  }

  add(...names) {
    names.forEach((name) => this.names.add(name));
  }

  remove(...names) {
    names.forEach((name) => this.names.delete(name));
  }

  toggle(name, force) {
    if (force) this.names.add(name);
    else this.names.delete(name);
  }

  contains(name) {
    return this.names.has(name);
  }
}

function fakeElement() {
  return {
    ariaHidden: "true",
    classList: new FakeClassList(),
    innerHTML: "",
    setAttribute(name, value) {
      if (name === "aria-hidden") this.ariaHidden = value;
    },
  };
}

const pane = fakeElement();
const detail = fakeElement();
const cards = [fakeElement(), fakeElement()];
cards[0].dataset = { id: "1" };
cards[1].dataset = { id: "2" };

globalThis.document = {
  querySelector(selector) {
    if (selector === "#pane") return pane;
    if (selector === "#detail") return detail;
    return null;
  },
  querySelectorAll(selector) {
    if (selector === ".cards") return cards;
    if (selector === ".cards.selected") {
      return cards.filter((card) => card.classList.contains("selected"));
    }
    return [];
  },
};

const { closeJobDetailPane, openJobDetailPane } = await import(
  "../../web/modules/job-detail-pane.js?test=shared-pane"
);

test("shared job detail pane preserves loading, selection, and rendered output", async () => {
  const loadCalls = [];
  await openJobDetailPane({
    paneSelector: "#pane",
    detailSelector: "#detail",
    cardsSelector: ".cards",
    selectedId: 2,
    getCardId: (card) => card.dataset.id,
    loadDetail: async (id) => {
      loadCalls.push(id);
      assert.match(detail.innerHTML, /class="loading-detail"/);
      return { html: "<h2>Selected job</h2>" };
    },
    errorTitle: "Job details unavailable",
  });

  assert.deepEqual(loadCalls, [2]);
  assert.equal(pane.classList.contains("open"), true);
  assert.equal(pane.classList.contains("has-selection"), true);
  assert.equal(pane.ariaHidden, "false");
  assert.equal(cards[0].classList.contains("selected"), false);
  assert.equal(cards[1].classList.contains("selected"), true);
  assert.equal(detail.innerHTML, "<h2>Selected job</h2>");
});

test("shared job detail pane closes and clears selected cards", () => {
  closeJobDetailPane({
    paneSelector: "#pane",
    selectedCardsSelector: ".cards.selected",
  });

  assert.equal(pane.classList.contains("open"), false);
  assert.equal(pane.ariaHidden, "true");
  assert.equal(cards.some((card) => card.classList.contains("selected")), false);
});
