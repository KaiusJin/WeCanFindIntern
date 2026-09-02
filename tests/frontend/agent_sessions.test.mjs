import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const indexSource = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
const moduleSource = await readFile(new URL("../../web/modules/agent.js", import.meta.url), "utf8");
const stylesSource = await readFile(new URL("../../web/styles.css", import.meta.url), "utf8");

test("conversation history exposes a confirmed delete flow", () => {
  assert.match(indexSource, /id="agent-delete-session-dialog"/);
  assert.match(indexSource, /id="confirm-agent-delete-session"/);
  assert.match(moduleSource, /data-delete-session-id/);
  assert.match(moduleSource, /method: "DELETE"/);
  assert.match(moduleSource, /openDeleteSessionDialog/);
  assert.match(moduleSource, /AGENT_TRASH_ICON/);
  assert.doesNotMatch(moduleSource, /🗑/);
  assert.match(moduleSource, /M3\.5 7h17/);
  assert.match(stylesSource, /\.agent-session-delete/);
  assert.match(stylesSource, /position: absolute;/);
  assert.match(stylesSource, /\.agent-delete-session-dialog \.settings-dialog-footer/);
});
