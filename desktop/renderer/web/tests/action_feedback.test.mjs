import assert from "node:assert/strict";
import test from "node:test";
import { createActionFeedback } from "../modules/feedback.js";

class Element extends EventTarget {
  textContent = "";
  disabled = false;
  dataset = {};
  attributes = new Map();
  setAttribute(name, value) { this.attributes.set(name, value); }
  removeAttribute(name) { this.attributes.delete(name); }
  querySelector() { return this.label || null; }
}

function control(options = {}) {
  const button = new Element();
  const scope = new Element();
  button.label = new Element();
  button.label.textContent = "Save changes";
  return { button, scope, feedback: createActionFeedback(button, { scope, ...options }) };
}

test("save success stays on its button until another edit", () => {
  const { button, scope, feedback } = control();
  const finish = feedback.begin();
  assert.equal(button.label.textContent, "Saving…");
  assert.equal(button.disabled, true);
  assert.equal(button.attributes.get("aria-busy"), "true");
  assert.equal(finish(true), true);
  assert.equal(button.label.textContent, "Saved");
  assert.equal(button.disabled, false);
  assert.equal(button.attributes.has("aria-busy"), false);
  scope.dispatchEvent(new Event("change"));
  assert.equal(button.label.textContent, "Save changes");
  assert.equal(button.dataset.feedback, undefined);
});

test("a failed request restores the original action and permits retry", () => {
  const { button, feedback } = control();
  assert.equal(feedback.begin()(false), false);
  assert.equal(button.label.textContent, "Save changes");
  assert.equal(button.disabled, false);
  assert.equal(button.dataset.feedback, undefined);
  assert.equal(feedback.begin()(true), true);
});

test("editing during a save prevents an older response from marking new input as saved", () => {
  const { button, scope, feedback } = control();
  const finish = feedback.begin();
  scope.dispatchEvent(new Event("input"));
  assert.equal(button.disabled, true);
  assert.equal(finish(true), false);
  assert.equal(button.label.textContent, "Save changes");
  assert.equal(button.disabled, false);
});

test("switching records detaches old requests from the new record's button feedback", () => {
  const { button, feedback } = control();
  const finishOld = feedback.begin();
  feedback.reset({ cancelPending: true });
  const finishNew = feedback.begin();
  assert.equal(finishOld(true), false);
  assert.equal(button.label.textContent, "Saving…");
  assert.equal(button.disabled, true);
  assert.equal(finishNew(true), true);
  assert.equal(button.label.textContent, "Saved");
  assert.equal(finishOld(false), false);
  assert.equal(button.label.textContent, "Saved");
});

test("repeated Copy clicks keep feedback until the latest success has timed out", t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { button, feedback } = control({ pending: "Copying…", success: "Copied", resetAfter: 1800 });
  feedback.begin()(true);
  t.mock.timers.tick(1200);
  feedback.begin()(true);
  t.mock.timers.tick(600);
  assert.equal(button.label.textContent, "Copied");
  t.mock.timers.tick(1200);
  assert.equal(button.label.textContent, "Save changes");
});
