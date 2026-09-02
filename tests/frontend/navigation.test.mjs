import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const indexSource = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
const shellSource = await readFile(new URL("../../web/app-shell.css", import.meta.url), "utf8");
const navigationSource = await readFile(new URL("../../web/modules/navigation.js", import.meta.url), "utf8");
const profileSource = await readFile(new URL("../../web/modules/profile.js", import.meta.url), "utf8");

const sidebarSource = indexSource.match(/<aside id="app-sidebar"[\s\S]*?<\/aside>/)?.[0] || "";

test("the app sidebar exposes the complete product navigation", () => {
  const expectedTabs = [
    "tab-jobs",
    "tab-waterlooworks",
    "tab-tracker",
    "tab-ats-score",
    "tab-ats-match",
    "tab-cover-letter",
    "tab-interview",
    "tab-agent",
    "tab-profile",
  ];

  for (const tabId of expectedTabs) {
    assert.match(sidebarSource, new RegExp(`data-tab="${tabId}"`));
  }

  assert.doesNotMatch(sidebarSource, /data-tab="tab-heatmap"/);
  assert.match(sidebarSource, /id="sidebar-open-settings"/);
  assert.match(sidebarSource, /<p class="nav-group-label">Discover<\/p>/);
  assert.match(sidebarSource, /<p class="nav-group-label">Career tools<\/p>/);
  assert.match(sidebarSource, /<p class="nav-group-label">Workspace<\/p>/);
  assert.match(sidebarSource, /<span>AI Assistant<\/span>/);
  assert.doesNotMatch(sidebarSource, /<span>Assistant<\/span>/);
});

test("page headers omit the requested section labels", () => {
  assert.doesNotMatch(
    indexSource,
    /<p class="section-kicker">(?:You|Discover|Career tools|Workspace|Manage)<\/p>/,
  );
  assert.match(indexSource, /<h1>AI Assistant<\/h1>/);
  assert.equal((indexSource.match(/class="section-heading-copy"/g) || []).length, 10);
});

test("Public Jobs owns List and Map while WaterlooWorks keeps a separate dataset", () => {
  assert.match(indexSource, /data-public-jobs-view="list"/);
  assert.match(indexSource, /data-public-jobs-view="map"/);
  assert.match(navigationSource, /"tab-heatmap"\s*:\s*"public-jobs\/map"/);
  assert.match(navigationSource, /targetTabId === "tab-heatmap" \? "tab-jobs"/);

  assert.match(indexSource, /id="job-list"/);
  assert.match(indexSource, /id="ww-job-list"/);
  assert.match(indexSource, /id="public-job-detail-pane"/);
  assert.match(indexSource, /id="ww-job-detail-pane"/);
});

test("desktop workspaces are bounded and narrow screens switch to overlays", () => {
  assert.match(shellSource, /#tab-jobs:not\(\[hidden\]\)/);
  assert.match(shellSource, /#tab-waterlooworks:not\(\[hidden\]\)/);
  assert.match(shellSource, /#tab-tracker:not\(\[hidden\]\)/);
  assert.match(shellSource, /#tab-profile:not\(\[hidden\]\)/);
  assert.match(shellSource, /@media \(max-width: 900px\)/);
  assert.match(shellSource, /body\.sidebar-open \.app-sidebar/);
  assert.match(shellSource, /\.job-detail-pane\.open/);
});

test("Profile exposes explicit resume imports without JSON export", () => {
  assert.match(indexSource, /id="profile-autofill-resume"[^>]*>Autofill From resume/);
  assert.match(indexSource, /id="profile-autofill-latex"[^>]*>Autofill From LaTeX/);
  assert.match(indexSource, /id="profile-autofill-dialog"/);
  assert.doesNotMatch(indexSource, /Import your resume|profile-import-panel/);
  assert.doesNotMatch(indexSource, /<span>0[1-8]<\/span>/);
  assert.doesNotMatch(indexSource, /Export JSON|profile\/export/);
  assert.doesNotMatch(profileSource, /profile\/export|Export JSON|profile-resume-list/);
});
