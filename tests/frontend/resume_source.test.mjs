import assert from "node:assert/strict";
import test from "node:test";

const elements = new Map();
let sourceInputs = [];

function element(initial = {}) {
  const listeners = new Map();
  return {
    checked: false,
    hidden: false,
    textContent: "",
    value: "",
    classList: { add() {}, remove() {} },
    addEventListener(type, listener) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(listener);
    },
    dispatch(type, event = {}) {
      for (const listener of listeners.get(type) || []) listener({ target: this, ...event });
    },
    ...initial,
  };
}

globalThis.window = {};
globalThis.document = {
  body: { classList: { add() {}, remove() {} } },
  documentElement: { classList: { add() {}, remove() {} } },
  querySelector(selector) {
    if (selector === "input[name='resume-source'][value=\"pdf\"]") return sourceInputs[1] || null;
    return elements.get(selector) || null;
  },
  querySelectorAll(selector) {
    return selector === "input[name='resume-source']" ? sourceInputs : [];
  },
};

const {
  setupProfileOrPdfResumeSource,
  setupResumePdfInput,
} = await import("../../web/modules/resume-source.js?test=shared-resume-source");

function registerResumeElements(prefix) {
  const selectors = {
    file: `#${prefix}-file`,
    dropzone: `#${prefix}-dropzone`,
    label: `#${prefix}-label`,
    text: `#${prefix}-text`,
    pdfSection: `#${prefix}-pdf-section`,
  };
  Object.values(selectors).forEach((selector) => elements.set(selector, element()));
  return selectors;
}

test("shared resume PDF input preserves extraction, input, and reset behavior", async () => {
  const selectors = registerResumeElements("upload");
  const extracted = [];
  let textChanges = 0;
  const source = setupResumePdfInput({
    fileInputSelector: selectors.file,
    dropzoneSelector: selectors.dropzone,
    fileLabelSelector: selectors.label,
    resumeTextSelector: selectors.text,
    extractingLabel: (file) => `Checking ${file.name}…`,
    extract: async () => ({ text: "Resume text", marker: "parsed" }),
    onExtract: (data) => extracted.push(data.marker),
    onTextChanged: () => { textChanges += 1; },
  });

  await source.extractFile({ name: "resume.pdf" });
  assert.equal(elements.get(selectors.label).textContent, "✓ Extracted from resume.pdf");
  assert.equal(elements.get(selectors.text).value, "Resume text");
  assert.deepEqual(extracted, ["parsed"]);

  elements.get(selectors.text).dispatch("input");
  assert.equal(textChanges, 1);

  source.reset();
  assert.equal(elements.get(selectors.file).value, "");
  assert.equal(elements.get(selectors.label).textContent, "Click or drag & drop resume PDF");
  assert.equal(elements.get(selectors.text).value, "");
});

test("shared Profile/PDF source keeps the same section visibility and profile loading", async () => {
  const selectors = registerResumeElements("picker");
  const profileInput = element({ checked: true });
  const pdfInput = element({ checked: false });
  sourceInputs = [profileInput, pdfInput];
  const profiles = [];

  const source = setupProfileOrPdfResumeSource({
    sourceInputSelector: "input[name='resume-source']",
    pdfSourceSelector: selectors.pdfSection,
    fileInputSelector: selectors.file,
    dropzoneSelector: selectors.dropzone,
    fileLabelSelector: selectors.label,
    resumeTextSelector: selectors.text,
    loadProfile: async () => ({ profile: { basics: { full_name: "Kai" } }, resume_text: "Profile resume" }),
    onProfileLoaded: (context) => profiles.push(context.profile.basics.full_name),
  });

  await source.loadProfileSource();
  assert.equal(source.isPdf(), false);
  assert.equal(elements.get(selectors.pdfSection).hidden, true);
  assert.equal(elements.get(selectors.text).value, "Profile resume");
  assert.ok(profiles.includes("Kai"));

  profileInput.checked = false;
  pdfInput.checked = true;
  elements.get(selectors.text).value = "stale";
  source.sync({ resetPdf: true });
  assert.equal(source.isPdf(), true);
  assert.equal(elements.get(selectors.pdfSection).hidden, false);
  assert.equal(elements.get(selectors.text).value, "");
  assert.equal(elements.get(selectors.label).textContent, "Click or drag & drop resume PDF");
});
