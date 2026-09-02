import {
  $,
  setupDropzone,
  showErrorDialog,
} from "./helpers.js?v=20260901-error-dialog-minimal-v1";

const DEFAULT_FILE_LABEL = "Click or drag & drop resume PDF";

export async function extractResumePdf(file) {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch("/api/v1/resumes/extract-pdf", { method: "POST", body: form });
  const result = await response.json().catch(() => ({}));
  if (!response.ok || !result.ok || !result.text) {
    throw new Error(result.detail || result.error || "Resume extraction failed.");
  }
  return result;
}

export async function loadProfileContext() {
  const response = await fetch("/api/v1/profile/context");
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.detail || "Could not load Profile.");
  return result;
}

export function setupResumePdfInput({
  fileInputSelector,
  dropzoneSelector,
  fileLabelSelector,
  resumeTextSelector,
  extractingLabel = (file) => `Extracting from ${file.name}…`,
  defaultFileLabel = DEFAULT_FILE_LABEL,
  clearFileInputOnError = false,
  clearTextOnError = false,
  extract = extractResumePdf,
  onExtract,
  onTextChanged,
  onReset,
}) {
  const fileInput = $(fileInputSelector);
  const fileLabel = $(fileLabelSelector);
  const resumeText = $(resumeTextSelector);

  async function extractFile(file) {
    if (!file) return;
    if (fileLabel) fileLabel.textContent = extractingLabel(file);
    try {
      const data = await extract(file);
      if (resumeText) resumeText.value = data.text;
      if (fileLabel) fileLabel.textContent = `✓ Extracted from ${file.name}`;
      await onExtract?.(data);
    } catch (error) {
      if (clearFileInputOnError && fileInput) fileInput.value = "";
      if (clearTextOnError && resumeText) resumeText.value = "";
      if (fileLabel) fileLabel.textContent = defaultFileLabel;
      showErrorDialog(error, { title: "Resume upload failed" });
    }
  }

  fileInput?.addEventListener("change", (event) => extractFile(event.target.files?.[0]));
  setupDropzone($(dropzoneSelector), (files) => extractFile(files[0]));
  resumeText?.addEventListener("input", () => onTextChanged?.());

  function reset({ clearFileInput = true, clearText = true } = {}) {
    if (clearFileInput && fileInput) fileInput.value = "";
    if (fileLabel) fileLabel.textContent = defaultFileLabel;
    if (clearText && resumeText) resumeText.value = "";
    onReset?.();
  }

  return { extractFile, reset };
}

export function setupProfileOrPdfResumeSource({
  sourceInputSelector,
  pdfSourceSelector,
  additionalPdfSourceSelectors = [],
  fileInputSelector,
  dropzoneSelector,
  fileLabelSelector,
  resumeTextSelector,
  loadProfile = loadProfileContext,
  onProfileLoaded,
  onExtract,
  onResetPdf,
}) {
  const resumeText = $(resumeTextSelector);
  const pdfInput = document.querySelector(`${sourceInputSelector}[value="pdf"]`);
  const pdfUpload = setupResumePdfInput({
    fileInputSelector,
    dropzoneSelector,
    fileLabelSelector,
    resumeTextSelector,
    clearTextOnError: true,
    onExtract,
  });

  function isPdf() {
    return pdfInput?.checked ?? false;
  }

  async function loadProfileSource() {
    try {
      const context = await loadProfile();
      if (resumeText) resumeText.value = context.resume_text || "";
      await onProfileLoaded?.(context);
    } catch (error) {
      if (resumeText) resumeText.value = "";
      showErrorDialog(error, { title: "Could not load Profile" });
    }
  }

  function sync({ resetPdf = false } = {}) {
    const usePdf = isPdf();
    [pdfSourceSelector, ...additionalPdfSourceSelectors].forEach((selector) => {
      const section = $(selector);
      if (section) section.hidden = !usePdf;
    });
    if (usePdf) {
      if (resetPdf) {
        pdfUpload.reset({ clearFileInput: false });
        onResetPdf?.();
      }
    } else {
      void loadProfileSource();
    }
  }

  document.querySelectorAll(sourceInputSelector).forEach((input) => {
    input.addEventListener("change", () => sync({ resetPdf: true }));
  });
  sync({ resetPdf: true });

  return { isPdf, loadProfileSource, pdfUpload, sync };
}
