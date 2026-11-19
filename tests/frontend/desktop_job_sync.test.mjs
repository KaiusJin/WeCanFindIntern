import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const moduleSource = await readFile(
  new URL("../../web/modules/desktop-job-sync.js", import.meta.url),
  "utf8",
);
const moduleUrl = `data:text/javascript;base64,${Buffer.from(moduleSource).toString("base64")}`;
const {
  jobSyncStatusView,
  startDesktopJobSyncMonitor,
} = await import(moduleUrl);

test("sync status shows the installed feed version", () => {
  const view = jobSyncStatusView({
    enabled: true,
    running: false,
    last_result: { status: "success", version: 7 },
  });
  assert.equal(view.state, "success");
  assert.match(view.text, /v7/);
});

test("monitor refreshes jobs when sync transitions to finished", async () => {
  const statuses = [
    { sync: { enabled: true, running: true, last_finished_at: null } },
    {
      sync: {
        enabled: true,
        running: false,
        last_finished_at: "2026-09-01T18:34:38Z",
        last_result: {
          status: "success",
          version: 7,
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
      getSyncStatus: async () => statuses.shift(),
    },
    setInterval: (callback) => {
      intervalCallback = callback;
      return 1;
    },
    clearInterval: () => {},
  };

  const stop = startDesktopJobSyncMonitor({
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
