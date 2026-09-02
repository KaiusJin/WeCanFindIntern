import assert from "node:assert/strict";
import test from "node:test";

const { createDebouncedAction } = await import(
  "../../web/modules/timing.js?test=shared-timing"
);

test("shared debounced action keeps only the latest scheduled call", async () => {
  let calls = 0;
  const schedule = createDebouncedAction(() => { calls += 1; });

  schedule(10);
  schedule(10);
  schedule(10);
  await new Promise((resolve) => setTimeout(resolve, 25));

  assert.equal(calls, 1);
});
