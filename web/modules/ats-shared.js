import {
  $,
  escapeHtml,
  fetchWithTimeout,
  responseErrorMessage,
} from "./helpers.js?v=20260901-error-dialog-minimal-v1";
import { setupResumePdfInput } from "./resume-source.js?v=20260902-shared-resume-source-v1";
import { validateAiConfig } from "./settings.js?v=20260902-shared-components-v1";

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

export function createAtsCommentary({
  defaultMessage,
  statusSelector,
  resultSelector,
  summarySelector,
  strengthsBlockSelector,
  strengthsSelector,
  improvementsSelector,
}) {
  let requestId = 0;

  function reset(message = defaultMessage) {
    $(statusSelector).textContent = message;
    $(statusSelector).hidden = false;
    $(resultSelector).hidden = true;
  }

  function render(commentary) {
    $(statusSelector).hidden = true;
    $(resultSelector).hidden = false;
    $(summarySelector).textContent = commentary.summary;
    const strengths = commentary.strengths || [];
    $(strengthsBlockSelector).hidden = !strengths.length;
    $(strengthsSelector).innerHTML = strengths
      .map((item) => `<li>${escapeHtml(item)}</li>`)
      .join("");
    $(improvementsSelector).innerHTML = (commentary.improvements || [])
      .map((item) => `<li>${escapeHtml(item)}</li>`)
      .join("");
  }

  function invalidate() {
    requestId += 1;
    reset();
  }

  async function generate(endpoint, payload) {
    const activeRequestId = ++requestId;
    let config;
    try {
      config = validateAiConfig();
    } catch (error) {
      if (activeRequestId === requestId) reset(error.message);
      return;
    }

    reset("Generating personalized AI feedback…");
    try {
      const response = await requestAtsDiagnostic(
        endpoint,
        {
          ...payload,
          provider: config.provider,
          model_name: config.model_name,
          api_key: config.api_key,
          api_base: config.api_base || "",
        },
        "AI feedback could not be generated.",
      );
      if (activeRequestId !== requestId) return;
      if (!response.ok || !response.commentary) {
        reset(response.error || "AI feedback could not be generated right now.");
        return;
      }
      render(response.commentary);
    } catch (error) {
      if (activeRequestId === requestId) {
        reset(error.message || "AI feedback could not be generated right now.");
      }
    }
  }

  return { generate, invalidate, render, reset };
}

export function setupAtsResumeSource({
  fileInputSelector,
  dropzoneSelector,
  fileLabelSelector,
  resumeTextSelector,
  onExtract,
  onTextChanged,
}) {
  return setupResumePdfInput({
    fileInputSelector,
    dropzoneSelector,
    fileLabelSelector,
    resumeTextSelector,
    extractingLabel: (file) => `Extracting and checking ${file.name}…`,
    clearFileInputOnError: true,
    onExtract,
    onTextChanged,
  });
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
