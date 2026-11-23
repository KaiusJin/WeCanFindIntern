import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { stylesSource } from "./stylesheets.mjs";

const indexSource = await readFile(new URL("../index.html", import.meta.url), "utf8");
const moduleSource = await readFile(new URL("../modules/agent.js", import.meta.url), "utf8");
const jobsSource = await readFile(new URL("../modules/jobs.js", import.meta.url), "utf8");
const waterlooWorksSource = await readFile(new URL("../modules/waterlooworks.js", import.meta.url), "utf8");

test("conversation history exposes a confirmed delete flow", () => {
  assert.doesNotMatch(indexSource, /id="agent-delete-session-dialog"/);
  assert.match(moduleSource, /async function deleteConversation[\s\S]*?await confirmDecision\([\s\S]*?if \(!confirmed[\s\S]*?method: "DELETE"/);
  assert.match(moduleSource, /data-delete-session-id/);
  assert.match(moduleSource, /AGENT_TRASH_ICON/);
  assert.doesNotMatch(moduleSource, /🗑/);
  assert.match(moduleSource, /M3\.5 7h17/);
  assert.match(stylesSource, /\.agent-session-delete/);
  assert.match(stylesSource, /position: absolute;/);
});

test("only recommendation and search results render job cards", () => {
  assert.doesNotMatch(moduleSource, /call\.tool_name === "compare_jobs"[\s\S]*ranked_jobs/);
  assert.match(moduleSource, /call\.tool_name === "recommend_jobs"[\s\S]*recommendations/);
  assert.match(moduleSource, /call\.tool_name === "search_jobs"[\s\S]*waterloo_work/);
  assert.doesNotMatch(moduleSource, /call\.tool_name === "analyse_job"/);
  assert.doesNotMatch(moduleSource, /call\.tool_name === "get_job_details"/);
});

test("job-card view actions keep the conversation active beside job details", () => {
  assert.doesNotMatch(indexSource, /id="agent-job-detail-backdrop"/);
  assert.match(indexSource, /id="agent-job-detail-pane"/);
  assert.match(moduleSource, /loadWaterlooWorksJobDetail/);
  assert.match(moduleSource, /loadPublicJobDetail/);
  assert.doesNotMatch(moduleSource, /switchTab\("tab-(?:waterlooworks|jobs)"\)/);
  assert.match(jobsSource, /export \{[\s\S]*loadPublicJobDetail/);
  assert.match(waterlooWorksSource, /export \{[\s\S]*loadWaterlooWorksJobDetail/);
  assert.match(moduleSource, /openAssistantPanel\("agent-job-detail-pane"\)/);
});

test("attachment and memory panels leave the conversation interactive", () => {
  assert.match(indexSource, /<aside[^>]+id="agent-attach-dialog"[^>]+hidden/);
  assert.match(indexSource, /<aside[^>]+id="agent-memory-dialog"[^>]+hidden/);
  assert.doesNotMatch(moduleSource, /showModal\(\)/);
  assert.doesNotMatch(indexSource, /id="agent-attachment-preview"/);
  assert.doesNotMatch(moduleSource, /#agent-attachment-preview/);
});

test("history closes after a selection and Career Memory remains available from the conversation toolbar", () => {
  assert.match(indexSource, /<details id="agent-history"[^>]*>/);
  assert.match(indexSource, /aria-controls="agent-session-list"/);
  assert.match(indexSource, /id="agent-memory-trigger"[\s\S]*?aria-controls="agent-memory-dialog"/);
  assert.match(indexSource, /id="agent-memory-dialog-count"/);
  assert.doesNotMatch(indexSource, /id="agent-memory-count"/);
  assert.match(moduleSource, /if \(sessionButton\) \{\s*historyMenu\.open = false;[\s\S]*?switchSession\(sessionButton.dataset.sessionId\)/);
});
