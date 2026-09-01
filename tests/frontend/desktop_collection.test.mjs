import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const moduleSource = await readFile(
  new URL("../../web/modules/desktop-collection.js", import.meta.url),
  "utf8",
);
const moduleUrl = `data:text/javascript;base64,${Buffer.from(moduleSource).toString("base64")}`;
const {
  collectionStatusView,
  startDesktopCollectionMonitor,
} = await import(moduleUrl);

test("partial collection status reports created and unavailable queries", () => {
  const view = collectionStatusView({
    enabled: true,
    running: false,
    last_result: {
      status: "partial",
      database_stats: { created: 1619 },
      query_stats: { failed: 2, skipped: 74 },
    },
  });

  assert.equal(view.state, "partial");
  assert.match(view.text, /1,619 jobs/);
  assert.match(view.text, /76 queries unavailable/);
});

test("monitor refreshes jobs when collection transitions to finished", async () => {
  const statuses = [
    { collection: { enabled: true, running: true, last_finished_at: null } },
    {
      collection: {
        enabled: true,
        running: false,
        last_finished_at: "2026-09-01T18:34:38Z",
        last_result: {
          status: "success",
          database_stats: { created: 10 },
          query_stats: { failed: 0, skipped: 0 },
        },
      },
    },
  ];
  let intervalCallback;
  let refreshCount = 0;
  globalThis.document = {
    visibilityState: "visible",
    querySelector: () => null,
    addEventListener: () => {},
    removeEventListener: () => {},
  };
  globalThis.window = {
    weCanFindInternDesktop: {
      getCollectionStatus: async () => statuses.shift(),
    },
    setInterval: (callback) => {
      intervalCallback = callback;
      return 1;
    },
    clearInterval: () => {},
  };

  const stop = startDesktopCollectionMonitor({
    refreshJobs: async () => {
      refreshCount += 1;
    },
  });
  await new Promise(setImmediate);
  intervalCallback();
  await new Promise(setImmediate);

  assert.equal(refreshCount, 1);
  stop();
});
