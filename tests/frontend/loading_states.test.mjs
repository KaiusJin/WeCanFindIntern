import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const indexSource = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
const stylesSource = await readFile(new URL("../../web/styles.css", import.meta.url), "utf8");

test("career tool loading states share the centered inline layout", () => {
  for (const id of [
    "ats-score-loading",
    "ats-match-loading",
    "interview-loading",
    "cl-loading",
  ]) {
    assert.match(
      indexSource,
      new RegExp(`id="${id}" class="loading-state"`),
    );
  }

  assert.match(stylesSource, /\.loading-state\s*\{[\s\S]*?display:\s*flex;/);
  assert.match(stylesSource, /\.loading-state\s*\{[\s\S]*?align-items:\s*center;/);
  assert.match(stylesSource, /\.loading-state\s*\{[\s\S]*?justify-content:\s*center;/);
  assert.match(stylesSource, /\.loading-state\s*>\s*span\s*\{[\s\S]*?white-space:\s*nowrap;/);
  assert.match(indexSource, /styles\.css\?v=20260902-memory-alignment-v1/);
});
