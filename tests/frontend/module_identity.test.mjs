import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import test from "node:test";

const moduleDirectory = new URL("../../web/modules/", import.meta.url);
const moduleFiles = (await readdir(moduleDirectory)).filter((name) => name.endsWith(".js"));
const sources = await Promise.all(moduleFiles.map(async (name) => ({
  name,
  source: await readFile(new URL(name, moduleDirectory), "utf8"),
})));

test("each local module has one canonical browser URL", () => {
  const urlsByModule = new Map();
  for (const { source } of sources) {
    const imports = source.matchAll(/(?:from\s*|import\()\s*["'](\.\/[^"']+\.js(?:\?v=[^"']+)?)['"]/g);
    for (const match of imports) {
      const url = match[1];
      const modulePath = url.split("?")[0];
      const urls = urlsByModule.get(modulePath) || new Set();
      urls.add(url);
      urlsByModule.set(modulePath, urls);
    }
  }

  const conflicts = [...urlsByModule.entries()]
    .filter(([, urls]) => urls.size > 1)
    .map(([modulePath, urls]) => `${modulePath}: ${[...urls].join(", ")}`);
  assert.deepEqual(conflicts, []);
});
