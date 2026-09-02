import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const interviewSource = await readFile(
  new URL("../../web/modules/interview.js", import.meta.url),
  "utf8",
);
const mainSource = await readFile(
  new URL("../../web/modules/main.js", import.meta.url),
  "utf8",
);

test("interview drafts, recordings, and reports are isolated per question", () => {
  assert.match(interviewSource, /responses:\s*new Map\(\)/);
  assert.match(interviewSource, /function responseFor\(index\)/);
  assert.match(
    interviewSource,
    /responseFor\(interviewState\.currentIndex\)\.text\s*=\s*event\.target\.value/,
  );
  assert.match(
    interviewSource,
    /#interview-answer-text"\)\.value\s*=\s*responseFor\(index\)\.text/,
  );
  assert.match(interviewSource, /response\.recordedBlob\s*=\s*recordedBlob/);
  assert.match(interviewSource, /renderAnalysisReport\(responseFor\(index\)\.analysis\)/);
  assert.doesNotMatch(interviewSource, /interviewState\.recordedBlob/);
});

test("analyze finishes an active recording without requiring a separate stop click", () => {
  const analyzeHandler = interviewSource.match(
    /\$\("#btn-analyze-answer"\)[\s\S]*?\n\}\);\n\n\$\("#interview-answer-text"\)/,
  )?.[0] || "";

  assert.match(analyzeHandler, /await stopActiveRecording\(\)/);
  assert.match(analyzeHandler, /const answerText = response\.text\.trim\(\)/);
  assert.ok(
    analyzeHandler.indexOf("await stopActiveRecording()")
      < analyzeHandler.indexOf("const answerText = response.text.trim()"),
  );
  assert.match(interviewSource, /"Stop & analyze ↗"/);
});

test("record button labels reflect the active question only", () => {
  assert.match(
    interviewSource,
    /startButton\.textContent = response\.recordedBlob \? "↺ Re-record" : "🎙 Start recording"/,
  );
  assert.match(interviewSource, /\? "Analyzing…"/);
  assert.doesNotMatch(interviewSource, /Transcribing audio locally/);
  assert.match(mainSource, /interview-answer-flow-v2/);
});
