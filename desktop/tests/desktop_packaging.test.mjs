import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const forgeConfig = require(path.join(repositoryRoot, "desktop", "forge.config.js"));

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
