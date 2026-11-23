import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { stylesSource } from "./stylesheets.mjs";

const indexSource = await readFile(new URL("../index.html", import.meta.url), "utf8");
const navigationSource = await readFile(new URL("../modules/navigation.js", import.meta.url), "utf8");
const profileSource = await readFile(new URL("../modules/profile.js", import.meta.url), "utf8");
const settingsSource = await readFile(new URL("../modules/settings.js", import.meta.url), "utf8");
const trackerSource = await readFile(new URL("../modules/tracker.js", import.meta.url), "utf8");

const toolbarSource = indexSource.match(/<nav class="toolbar-navigator"[\s\S]*?<\/nav>/)?.[0] || "";

test("the toolbar exposes every destination in wide and narrow windows", () => {
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
    "tab-settings",
  ];

  for (const tabId of expectedTabs) {
    assert.match(toolbarSource, new RegExp(`data-tab="${tabId}"`));
    assert.match(toolbarSource, new RegExp(`<option value="${tabId}"`));
  }

  assert.doesNotMatch(toolbarSource, /data-tab="tab-heatmap"/);
  assert.equal((indexSource.match(/class="app-nav"/g) || []).length, 1);
  assert.match(indexSource, /id="toolbar-nav-host"/);
  assert.match(toolbarSource, /<select id="workspace-picker" aria-label="Switch workspace"/);
  assert.doesNotMatch(indexSource, /id="(?:app-sidebar|toggle-app-sidebar|sidebar-nav-host)"/);
  for (const match of toolbarSource.matchAll(/class="[^"]*\bgroup-toggle\b[^"]*"[^>]*aria-controls="([^"]+)"/g)) {
    assert.ok(toolbarSource.includes(`id="${match[1]}"`));
  }
  const directDestinations = [...toolbarSource.matchAll(/class="nav-group direct-group">[\s\S]*?data-tab="([^"]+)"/g)].map(match => match[1]);
  assert.deepEqual(directDestinations, ["tab-jobs", "tab-waterlooworks", "tab-tracker", "tab-agent", "tab-profile", "tab-settings"]);
  assert.match(toolbarSource, /<span>LinkedIn \/ Indeed<\/span>/);
  assert.match(toolbarSource, /<optgroup label="Career tools">/);
  assert.match(toolbarSource, /<span>AI Assistant<\/span>/);
  assert.doesNotMatch(toolbarSource, /<span>Assistant<\/span>/);
});

test("workspaces expose headings except the intentionally untitled WaterlooWorks page", () => {
  const panes = [...indexSource.matchAll(/<div id="(tab-[^"]+)" class="tab-pane[^"]*"/g)];
  assert.equal(panes.length, 11);
  for (const [index, pane] of panes.entries()) {
    const section = indexSource.slice(pane.index, panes[index + 1]?.index ?? indexSource.indexOf("</main>"));
    assert.equal((section.match(/<h1[ >]/g) || []).length, pane[1] === "tab-waterlooworks" ? 0 : 1, pane[1]);
  }
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
  assert.match(navigationSource, /new Event\("assistant:leave"\)/);
  assert.match(navigationSource, /closeAgentDialogsWhenLeaving\(targetTabId\);\s*const navTarget/);
});

test("desktop workspaces keep the index and reader independently scrollable", () => {
  assert.match(stylesSource, /#tab-jobs:not\(\[hidden\]\)/);
  assert.match(stylesSource, /#tab-waterlooworks:not\(\[hidden\]\)/);
  assert.match(stylesSource, /#tab-tracker:not\(\[hidden\]\)/);
  assert.match(stylesSource, /#tab-profile:not\(\[hidden\]\)/);
  assert.match(stylesSource, /\.posting-workspace:has\(> \.job-detail-pane\.open\)/);
  assert.match(stylesSource, /\.results-scroll-content/);
});

test("Applications keeps stage and date edits reachable in the compact list and detail view", () => {
  assert.match(indexSource, /class="applications-list-pane"/);
  assert.match(indexSource, /id="tracker-detail-drawer" class="tracker-detail-drawer applications-detail-pane"/);
  assert.match(indexSource, /class="field-group job-content-field tracker-link-field"/);
  assert.match(trackerSource, /class="tracker-role-cell" type="button" aria-controls="tracker-detail-drawer"/);
  assert.match(trackerSource, /class="tracker-inline-date"/);
  assert.match(trackerSource, /class="tracker-inline-stage stage-/);
  assert.match(stylesSource, /@container application-list/);
});

test("focus rings are limited to keyboard navigation targets", () => {
  assert.match(stylesSource, /button:focus-visible,\s*a:focus-visible,/);
  assert.match(stylesSource, /input\[type="checkbox"\]:focus-visible,\s*input\[type="radio"\]:focus-visible/);
  assert.doesNotMatch(
    stylesSource,
    /button:focus-visible,\s*a:focus-visible,\s*input:focus-visible,\s*select:focus-visible,\s*textarea:focus-visible/,
  );
  assert.match(stylesSource, /\.tracker-inline-date:(?:focus|is\(:hover, :focus\))\s*\{[^}]*outline:\s*0;/);
});

test("Profile exposes explicit resume imports without JSON export", () => {
  assert.match(indexSource, /id="profile-autofill-resume"[^>]*>(?:(?!<\/button>)[\s\S])*Import resume/);
  assert.match(indexSource, /id="profile-autofill-latex"[^>]*>(?:(?!<\/button>)[\s\S])*Import LaTeX/);
  assert.match(indexSource, /id="profile-autofill-dialog"/);
  assert.doesNotMatch(indexSource, /Import your resume|profile-import-panel/);
  assert.doesNotMatch(indexSource, /<span>0[1-8]<\/span>/);
  assert.doesNotMatch(indexSource, /Export JSON|profile\/export/);
  assert.doesNotMatch(profileSource, /profile\/export|Export JSON|profile-resume-list/);
});
