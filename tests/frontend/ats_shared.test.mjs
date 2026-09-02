import assert from "node:assert/strict";
import test from "node:test";

const elements = new Map();
globalThis.window = {};
globalThis.document = {
  body: { classList: { add() {}, remove() {} } },
  documentElement: { classList: { add() {}, remove() {} } },
  querySelector: (selector) => elements.get(selector) || null,
  querySelectorAll: () => [],
};

const { createAtsCommentary } = await import(
  "../../web/modules/ats-shared.js?test=shared-commentary"
);

function element() {
  return { hidden: false, innerHTML: "", textContent: "" };
}

test("shared ATS commentary preserves the existing status and result behavior", () => {
  const selectors = {
    statusSelector: "#status",
    resultSelector: "#result",
    summarySelector: "#summary",
    strengthsBlockSelector: "#strengths-block",
    strengthsSelector: "#strengths",
    improvementsSelector: "#improvements",
  };
  Object.values(selectors).forEach((selector) => elements.set(selector, element()));

  const commentary = createAtsCommentary({
    defaultMessage: "Calculate first.",
    ...selectors,
  });

  commentary.reset();
  assert.equal(elements.get("#status").textContent, "Calculate first.");
  assert.equal(elements.get("#status").hidden, false);
  assert.equal(elements.get("#result").hidden, true);

  commentary.render({
    summary: "Good evidence",
    strengths: ["Clear <impact>"],
    improvements: ["Add metrics"],
  });
  assert.equal(elements.get("#status").hidden, true);
  assert.equal(elements.get("#result").hidden, false);
  assert.equal(elements.get("#summary").textContent, "Good evidence");
  assert.equal(elements.get("#strengths-block").hidden, false);
  assert.equal(elements.get("#strengths").innerHTML, "<li>Clear &lt;impact&gt;</li>");
  assert.equal(elements.get("#improvements").innerHTML, "<li>Add metrics</li>");
});

test("shared ATS commentary hides an empty strengths section", () => {
  const commentary = createAtsCommentary({
    defaultMessage: "Calculate first.",
    statusSelector: "#status",
    resultSelector: "#result",
    summarySelector: "#summary",
    strengthsBlockSelector: "#strengths-block",
    strengthsSelector: "#strengths",
    improvementsSelector: "#improvements",
  });

  commentary.render({ summary: "No strengths", strengths: [], improvements: [] });

  assert.equal(elements.get("#strengths-block").hidden, true);
  assert.equal(elements.get("#strengths").innerHTML, "");
  assert.equal(elements.get("#improvements").innerHTML, "");
});
