import {
  $,
  clearFieldError,
  setupDropzone,
  showErrorToast,
  showFieldError,
  showToast,
} from "./helpers.js?v=20260901-error-dialog-minimal-v1";

const DEFAULT_FILE_LABEL = "Click or drag & drop resume PDF";
class ResumeFileError extends Error {}

export async function extractResumePdf(file) {
  if (!/\.pdf$/i.test(file.name)) throw new ResumeFileError("Choose a PDF file.");
  if (file.size > 8 * 1024 * 1024) throw new ResumeFileError("Choose a PDF of 8 MB or less.");
  const form = new FormData();
  form.append("file", file);
  const response = await fetch("/api/v1/resumes/extract-pdf", { method: "POST", body: form });
  const result = await response.json().catch(() => ({}));
  if ([400, 413, 415, 422].includes(response.status)) {
    throw new ResumeFileError(typeof result.detail === "string" ? result.detail : "Choose a readable, text-based PDF.");
  }
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
  clearTextOnError = false,
  extract = extractResumePdf,
  onExtract,
  onTextChanged,
  onReset,
}) {
  const fileInput = $(fileInputSelector);
  const fileLabel = $(fileLabelSelector);
  const resumeText = $(resumeTextSelector);
  const dropzone = $(dropzoneSelector);
  const removeButton = $(`[data-remove-for="${dropzone?.id}"]`);
  let uploadVersion = 0;
  let extracting = false;

  async function extractFile(file) {
    if (!file) return;
    const version = ++uploadVersion;
    extracting = true;
    clearFieldError(fileInput);
    if (clearTextOnError && resumeText) resumeText.value = "";
    if (removeButton) removeButton.hidden = false;
    if (fileLabel) fileLabel.textContent = extractingLabel(file);
    try {
      const data = await extract(file);
      if (version !== uploadVersion) return;
      if (resumeText) resumeText.value = data.text;
      clearFieldError(fileInput);
      clearFieldError(resumeText);
      if (fileLabel) fileLabel.textContent = `✓ Extracted from ${file.name}`;
      await onExtract?.(data);
    } catch (error) {
      if (version !== uploadVersion) return;
      if (fileInput) fileInput.value = "";
      if (clearTextOnError && resumeText) resumeText.value = "";
      if (fileLabel) fileLabel.textContent = defaultFileLabel;
      if (removeButton) removeButton.hidden = true;
      if (error instanceof ResumeFileError) showFieldError(fileInput, error.message, { focus: !fileInput.closest(".tab-pane[hidden]") });
      else showErrorToast(error, { title: "Resume upload failed" });
    } finally {
      if (version === uploadVersion) extracting = false;
    }
  }

  fileInput?.addEventListener("change", (event) => extractFile(event.target.files?.[0]));
  setupDropzone(dropzone, (files) => extractFile(files[0]));
  resumeText?.addEventListener("input", () => {
    clearFieldError(fileInput);
    onTextChanged?.();
  });

  function reset({ clearFileInput = true, clearText = true } = {}) {
    uploadVersion += 1;
    extracting = false;
    clearFieldError(fileInput);
    clearFieldError(resumeText);
    if (clearFileInput && fileInput) fileInput.value = "";
    if (fileLabel) fileLabel.textContent = defaultFileLabel;
    if (removeButton) removeButton.hidden = true;
    if (clearText && resumeText) resumeText.value = "";
    onReset?.();
  }

  removeButton?.addEventListener("click", () => {
    reset();
    fileInput?.focus();
  });

  return { extractFile, isExtracting: () => extracting, reset };
}

export function setupProfileOrPdfResumeSource({
  sourceInputSelector,
  sourceControlSelector,
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
  const sourceControl = sourceControlSelector && $(sourceControlSelector);
  const profileTab = sourceControl?.querySelector('[role="tab"]');
  let profileVersion = 0;
  let loadingProfile = false;
  const pdfUpload = setupResumePdfInput({
    fileInputSelector,
    dropzoneSelector,
    fileLabelSelector,
    resumeTextSelector,
    clearTextOnError: true,
    onExtract,
    onReset: onResetPdf,
  });

  function isPdf() {
    return pdfInput?.checked ?? false;
  }

  async function loadProfileSource() {
    const version = ++profileVersion;
    loadingProfile = true;
    try {
      const context = await loadProfile();
      if (isPdf() || version !== profileVersion) return;
      if (resumeText) resumeText.value = context.resume_text || "";
      clearFieldError(profileTab);
      await onProfileLoaded?.(context);
    } catch (error) {
      if (isPdf() || version !== profileVersion) return;
      if (resumeText) resumeText.value = "";
      showErrorToast(error, { title: "Could not load Profile" });
    } finally {
      if (version === profileVersion) loadingProfile = false;
    }
  }

  function sync({ resetPdf = false } = {}) {
    const usePdf = isPdf();
    clearFieldError(profileTab);
    [pdfSourceSelector, ...additionalPdfSourceSelectors].forEach((selector) => {
      const section = $(selector);
      if (section) section.hidden = !usePdf;
    });
    if (usePdf) {
      if (resetPdf) {
        pdfUpload.reset();
      }
    } else {
      pdfUpload.reset();
      void loadProfileSource();
    }
  }

  document.querySelectorAll(sourceInputSelector).forEach((input) => {
    input.addEventListener("change", () => sync({ resetPdf: true }));
  });
  sync({ resetPdf: true });
  document.addEventListener("wcfi:profile-saved", () => {
    if (!isPdf()) void loadProfileSource();
  });

  function showSourceError(profileMessage, pdfMessage) {
    showFieldError(isPdf() ? $(fileInputSelector) : profileTab, isPdf() ? pdfMessage : profileMessage);
  }

  function readyForGeneration() {
    const pdf = isPdf();
    if (pdf ? !pdfUpload.isExtracting() : !loadingProfile) return true;
    showToast(pdf ? "Your resume PDF is still being read." : "Your Profile is still loading.", { variant: "info" });
    return false;
  }

  function resetToProfile() {
    if (isPdf()) {
      document.querySelector(`${sourceInputSelector}[value="profile"]`).click();
    } else {
      sync();
    }
  }

  return { isPdf, loadProfileSource, pdfUpload, readyForGeneration, resetToProfile, showSourceError, sync };
}
