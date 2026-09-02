import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const indexSource = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
const shellSource = await readFile(new URL("../../web/app-shell.css", import.meta.url), "utf8");
const stylesSource = await readFile(new URL("../../web/styles.css", import.meta.url), "utf8");
const navigationSource = await readFile(new URL("../../web/modules/navigation.js", import.meta.url), "utf8");
const heatmapSource = await readFile(new URL("../../web/modules/heatmap.js", import.meta.url), "utf8");
const waterlooWorksSource = await readFile(new URL("../../web/modules/waterlooworks.js", import.meta.url), "utf8");
const profileSource = await readFile(new URL("../../web/modules/profile.js", import.meta.url), "utf8");
const settingsSource = await readFile(new URL("../../web/modules/settings.js", import.meta.url), "utf8");
const trackerSource = await readFile(new URL("../../web/modules/tracker.js", import.meta.url), "utf8");

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
  assert.match(indexSource, /class="results-scroll-content">[\s\S]*id="job-list"/);
  assert.match(indexSource, /id="ww-jobs-scroll-content" class="results-scroll-content"/);
  assert.match(indexSource, /id="public-job-detail-pane"/);
  assert.match(indexSource, /id="ww-job-detail-pane"/);
});

test("shared modules use one cache-busted URL", () => {
  assert.match(heatmapSource, /navigation\.js\?v=20260901-app-shell-v4/);
  assert.doesNotMatch(heatmapSource, /from "\.\/navigation\.js";/);
  assert.match(waterlooWorksSource, /helpers\.js\?v=20260901-error-dialog-minimal-v1/);
  assert.doesNotMatch(waterlooWorksSource, /helpers\.js\?v=20260901-waterlooworks-filters-v1/);
});

test("saved embedding settings configure the resident recommendation indexer", () => {
  assert.match(settingsSource, /fetch\("\/api\/v1\/agent\/embedding-config"/);
  assert.match(settingsSource, /await syncEmbeddingConfig\(\);/);
  assert.match(settingsSource, /syncEmbeddingConfig\(\{ showError: true \}\)/);
  assert.doesNotMatch(indexSource, /id="settings-save-feedback"/);
  assert.doesNotMatch(settingsSource, /settings-save-feedback|Saved in the operating-system secure store|Saved in this browser/);
});

test("leaving the AI Agent closes only its open dialogs", () => {
  assert.match(navigationSource, /function closeAgentDialogsWhenLeaving\(targetTabId\)/);
  assert.match(navigationSource, /activeTabId !== "tab-agent" \|\| targetTabId === "tab-agent"/);
  assert.match(navigationSource, /#agent-attach-dialog\[open\], #agent-delete-session-dialog\[open\]/);
  assert.match(navigationSource, /closeAgentDialogsWhenLeaving\(targetTabId\);\s*const navTarget/);
});

test("desktop workspaces are bounded and narrow screens switch to overlays", () => {
  assert.match(shellSource, /#tab-jobs:not\(\[hidden\]\)/);
  assert.match(shellSource, /#tab-waterlooworks:not\(\[hidden\]\)/);
  assert.match(shellSource, /#tab-tracker:not\(\[hidden\]\)/);
  assert.match(shellSource, /#tab-profile:not\(\[hidden\]\)/);
  assert.match(shellSource, /@media \(max-width: 900px\)/);
  assert.match(shellSource, /body\.sidebar-open \.app-sidebar/);
  assert.match(shellSource, /\.job-detail-pane\.open/);
  assert.match(shellSource, /\.results-scroll-content/);
});

test("Applications split view lets the table scroll within its pane", () => {
  assert.match(indexSource, /class="field-group job-content-field tracker-link-field"/);
  assert.match(indexSource, /app-shell\.css\?v=20260902-memory-alignment-v1/);
  assert.doesNotMatch(indexSource, /<th><\/th><\/tr><\/thead>/);
  assert.doesNotMatch(trackerSource, /tracker-row-menu|Open application/);
  assert.match(
    shellSource,
    /\.applications-list-pane \.tracker-table\s*\{[\s\S]*?min-width:\s*1000px;/,
  );
  assert.match(
    shellSource,
    /\.tracker-stats-grid\s*\{[\s\S]*?grid-template-columns:\s*repeat\(6, minmax\(0, 1fr\)\) minmax\(0, 1\.25fr\);/,
  );
  assert.match(
    shellSource,
    /\.tracker-detail-drawer\.applications-detail-pane \.tracker-status-grid\s*\{[\s\S]*?border:\s*0;/,
  );
  assert.match(
    shellSource,
    /\.applications-list-pane \.tracker-inline-date\s*\{[\s\S]*?max-width:\s*none;[\s\S]*?padding-left:\s*0;[\s\S]*?width:\s*100%;/,
  );
  assert.match(
    shellSource,
    /\.applications-list-pane \.tracker-table th:nth-child\(7\)\s*\{\s*width:\s*10%;\s*\}/,
  );
  assert.match(
    shellSource,
    /\.tracker-detail-drawer\.applications-detail-pane \.tracker-link-field\s*\{[\s\S]*?grid-column:\s*1 \/ -1;/,
  );
  assert.match(
    shellSource,
    /@media \(max-width: 1300px\)[\s\S]*?\.tracker-detail-drawer\.applications-detail-pane[\s\S]*?position:\s*fixed;/,
  );
});

test("focus rings are limited to keyboard navigation targets", () => {
  assert.match(shellSource, /button:focus-visible,\s*a:focus-visible,/);
  assert.match(shellSource, /input\[type="checkbox"\]:focus-visible,\s*input\[type="radio"\]:focus-visible/);
  assert.doesNotMatch(
    shellSource,
    /button:focus-visible,\s*a:focus-visible,\s*input:focus-visible,\s*select:focus-visible,\s*textarea:focus-visible/,
  );
  assert.match(stylesSource, /\.tracker-inline-date:focus\s*\{[\s\S]*?outline:\s*0;/);
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
