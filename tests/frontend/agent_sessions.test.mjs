import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const indexSource = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
const moduleSource = await readFile(new URL("../../web/modules/agent.js", import.meta.url), "utf8");
const mainSource = await readFile(new URL("../../web/modules/main.js", import.meta.url), "utf8");
const jobsSource = await readFile(new URL("../../web/modules/jobs.js", import.meta.url), "utf8");
const waterlooWorksSource = await readFile(new URL("../../web/modules/waterlooworks.js", import.meta.url), "utf8");
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

test("only recommendation and search results render job cards", () => {
  assert.doesNotMatch(moduleSource, /call\.tool_name === "compare_jobs"[\s\S]*ranked_jobs/);
  assert.match(moduleSource, /call\.tool_name === "recommend_jobs"[\s\S]*recommendations/);
  assert.match(moduleSource, /call\.tool_name === "search_jobs"[\s\S]*waterloo_work/);
  assert.doesNotMatch(moduleSource, /call\.tool_name === "analyse_job"/);
  assert.doesNotMatch(moduleSource, /call\.tool_name === "get_job_details"/);
  assert.match(indexSource, /main\.js\?v=20260901-agent-cards-v1/);
  assert.match(mainSource, /agent\.js\?v=20260901-agent-cards-v1/);
});

test("job-card view actions keep the Agent open and show a blurred JD drawer", () => {
  assert.match(indexSource, /id="agent-job-detail-backdrop"/);
  assert.match(indexSource, /id="agent-job-detail-pane"/);
  assert.match(moduleSource, /loadWaterlooWorksJobDetail/);
  assert.match(moduleSource, /loadPublicJobDetail/);
  assert.doesNotMatch(moduleSource, /switchTab\("tab-(?:waterlooworks|jobs)"\)/);
  assert.match(jobsSource, /export \{[\s\S]*loadPublicJobDetail/);
  assert.match(waterlooWorksSource, /export \{[\s\S]*loadWaterlooWorksJobDetail/);
  assert.match(stylesSource, /\.agent-job-detail-backdrop[\s\S]*backdrop-filter: blur\(7px\)/);
  assert.match(stylesSource, /body\.agent-job-detail-open \.agent-job-detail-pane/);
});

test("the Attach jobs dialog is hidden until it is opened", () => {
  assert.match(stylesSource, /\.agent-attach-dialog\[open\]\s*\{\s*display: flex;/);
  assert.match(indexSource, /styles\.css\?v=20260901-loading-layout-v1/);
  assert.doesNotMatch(indexSource, /id="agent-attachment-preview"/);
  assert.doesNotMatch(moduleSource, /#agent-attachment-preview/);
});
