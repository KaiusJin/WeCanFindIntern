import {
  $,
  escapeHtml,
  fetchWithTimeout,
  responseErrorMessage,
  setupDropzone,
  showErrorDialog,
} from "./helpers.js";
import { extractResumePdf } from "./resume-source.js";

const DEFAULT_FILE_LABEL = "Click or drag & drop resume PDF";

export function renderAtsBreakdown(selector, items = []) {
  $(selector).innerHTML = items.map((item) => {
    const maximum = Number(item.maximum || 0);
    const earned = Number(item.earned || 0);
    const percentage = maximum ? Math.round((earned / maximum) * 100) : 0;
    const score = item.status === "unavailable" ? "Not assessed" : `${earned}/${maximum}`;
    return `<article class="ats-breakdown-item ats-${escapeHtml(item.status)}">
      <div class="ats-breakdown-head"><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(score)}</span></div>
      <div class="ats-breakdown-track"><span style="width:${Math.max(0, Math.min(100, percentage))}%"></span></div>
      <p>${escapeHtml((item.evidence || []).join(" · "))}</p>
    </article>`;
  }).join("");
}

export function setupAtsResumeSource({
  fileInputSelector,
  dropzoneSelector,
  fileLabelSelector,
  resumeTextSelector,
  onExtract,
  onTextChanged,
}) {
  const fileInput = $(fileInputSelector);
  const fileLabel = $(fileLabelSelector);
  const resumeText = $(resumeTextSelector);

  async function extractFile(file) {
    if (!file) return;
    fileLabel.textContent = `Extracting and checking ${file.name}…`;
    try {
      const data = await extractResumePdf(file);
      resumeText.value = data.text;
      fileLabel.textContent = `✓ Extracted from ${file.name}`;
      await onExtract?.(data);
    } catch (error) {
      fileInput.value = "";
      fileLabel.textContent = DEFAULT_FILE_LABEL;
      showErrorDialog(error, { title: "Resume upload failed" });
    }
  }

  fileInput?.addEventListener("change", (event) => extractFile(event.target.files?.[0]));
  setupDropzone($(dropzoneSelector), (files) => extractFile(files[0]));
  resumeText?.addEventListener("input", () => onTextChanged?.());

  return {
    reset() {
      fileInput.value = "";
      fileLabel.textContent = DEFAULT_FILE_LABEL;
      resumeText.value = "";
    },
  };
}

export async function requestAtsDiagnostic(endpoint, payload, fallback) {
  const response = await fetchWithTimeout(
    endpoint,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
    30000,
  );
  if (!response.ok) throw new Error(await responseErrorMessage(response, fallback));
  return response.json();
}
