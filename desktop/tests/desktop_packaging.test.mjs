import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const forgeConfig = require(path.join(repositoryRoot, "desktop", "forge.config.js"));

test("the desktop sidecar receives a single renderer resource outside app.asar", () => {
  const { extraResource, ignore } = forgeConfig.packagerConfig;
  const renderer = path.join(repositoryRoot, "desktop", "renderer", "web");
  assert.deepEqual(extraResource.filter((entry) => path.basename(entry) === "web"), [renderer]);
  for (const file of ["index.html", "modules/main.js", "ui/tokens.css", "ui/icons.svg",
    "vendor/liquid-glass.js", "vendor/liquid-glass.css", "vendor/liquid-glass-LICENSE.txt"]) {
    assert.ok(fs.existsSync(path.join(renderer, file)), file);
  }
  assert.ok(ignore.some((pattern) => pattern.test("/renderer/node_modules/react/index.js")));
  assert.ok(ignore.some((pattern) => pattern.test("/renderer/web/index.html")));
  assert.ok(!ignore.some((pattern) => pattern.test("/src/main.js")));
});

test("macOS packaging seals the complete app with a certificate-free signature", () => {
  const signing = forgeConfig.packagerConfig.osxSign;

  assert.equal(signing.identity, "-");
  assert.equal(signing.identityValidation, false);
  assert.equal(signing.continueOnError, false);
  assert.equal(signing.preAutoEntitlements, false);
  assert.equal(signing.preEmbedProvisioningProfile, false);
  assert.equal(signing.strictVerify, true);
  assert.deepEqual(signing.optionsForFile("/tmp/example"), {
    hardenedRuntime: false,
    timestamp: "none",
  });
  assert.equal(forgeConfig.packagerConfig.osxNotarize, undefined);
});

test("the macOS disk image contains an English manual trust guide", () => {
  const dmgMaker = forgeConfig.makers.find((maker) => maker.name === "@electron-forge/maker-dmg");
  const appPath = "/tmp/WeCanFindIntern.app";
  const contents = dmgMaker.config.contents({ appPath });
  const guide = contents.find((entry) => entry.name === "First Launch Guide.txt");

  assert.ok(guide);
  assert.equal(contents.find((entry) => entry.path === appPath)?.type, "file");
  assert.equal(contents.find((entry) => entry.path === "/Applications")?.type, "link");
  assert.equal(fs.existsSync(guide.path), true);
  const guideText = fs.readFileSync(guide.path, "utf8");
  assert.match(guideText, /First Launch Guide/);
  assert.doesNotMatch(guideText, /[\u3400-\u9fff]/u);
});

test("desktop window materials support both native platforms and older Windows", () => {
  const { windowMaterial } = require("../src/window-style");
  assert.equal(windowMaterial("darwin", "23.0.0").vibrancy, "sidebar");
  assert.equal(windowMaterial("win32", "10.0.22621").backgroundMaterial, "mica");
  assert.deepEqual(windowMaterial("win32", "10.0.19045"), {});
});

test("the workspace bar reserves native controls and follows Windows appearance", () => {
  const { windowTitleBar } = require("../src/window-style");
  assert.equal(windowTitleBar("darwin", false).titleBarStyle, "hiddenInset");
  const light = windowTitleBar("win32", false);
  const dark = windowTitleBar("win32", true);
  assert.equal(light.titleBarStyle, "hidden");
  assert.equal(light.titleBarOverlay.height, dark.titleBarOverlay.height);
  assert.notEqual(light.titleBarOverlay.symbolColor, dark.titleBarOverlay.symbolColor);
  assert.deepEqual(windowTitleBar("linux", false), {});
});
