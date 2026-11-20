import { $, fillGlassInput, clearFieldError, escapeHtml, resumeContactUrl, fetchWithTimeout, responseErrorMessage, showErrorToast, showFieldError, showToast, createActionFeedback } from "./helpers.js?v=20260901-error-dialog-minimal-v1";
import { getAiConfigOrShowError } from "./settings.js?v=20260902-shared-components-v1";
import { setupProfileOrPdfResumeSource } from "./resume-source.js?v=20260902-shared-resume-source-v1";
import { readSseEvents } from "./sse.js";

// =========================================================
// SECTION 4: COVER LETTER GENERATOR & EXPORT
// =========================================================

let coverLetterProfile = null;
let coverLetterProgressTimer = null;
const contactFields = [
  ["full_name", "#cl-full-name", "full name"],
  ["email", "#cl-email", "email address"],
  ["phone", "#cl-phone", "phone number"],
  ["linkedin", "#cl-linkedin", "LinkedIn or portfolio"],
];

function profileContact() {
  const basics = coverLetterProfile?.basics || {};
  return {
    full_name: basics.full_name || "",
    email: basics.email || "",
    phone: basics.phone || "",
    linkedin: basics.linkedin_url || basics.github_url || basics.portfolio_url || "",
    address: [basics.city, basics.region, basics.country].filter(Boolean).join(", "),
  };
}

function coverLetterUserInfo() {
  const contact = profileContact();
  if (letterResumeSource.isPdf()) {
    for (const [field, selector] of contactFields) contact[field] = $(selector).value.trim();
  }
  return contact;
}

function validateCoverLetterInputs() {
  if (!$("#cl-jd-text").value.trim()) {
    showFieldError("#cl-jd-text", "Paste the target job description.");
    return false;
  }
  clearFieldError("#cl-jd-text");
  if (!letterResumeSource.readyForGeneration()) return false;
  if (!$("#cl-resume-text").value.trim()) {
    letterResumeSource.showSourceError("Add resume information to Profile or choose Resume PDF.", "Upload a text-based resume PDF and wait for extraction.");
    return false;
  }
  const contact = coverLetterUserInfo();
  if (!letterResumeSource.isPdf()) {
    const missing = contactFields.filter(([field]) => !contact[field]).map(([, , label]) => label);
    if (missing.length) {
      letterResumeSource.showSourceError(`Add ${missing.join(", ")} to Profile or choose Resume PDF.`);
      return false;
    }
    return true;
  }

  let valid = true;
  for (const [field, selector, label] of contactFields) {
    const input = $(selector);
    let message = "";
    if (!contact[field]) message = `Enter your ${label}.`;
    else if (input.validity.typeMismatch) message = "Enter a valid email address.";
    else if (field === "linkedin" && !resumeContactUrl(contact[field])) message = "Enter a LinkedIn or portfolio link.";
    if (message) {
      showFieldError(input, message, { focus: valid });
      valid = false;
    }
  }
  return valid;
}

function startCoverLetterProgress() {
  const message = $("#cl-loading-message");
  let stage = 0;
  const stages = [
    "Writer AI is drafting your cover letter…",
    "Reviewer AI is checking every factual claim…",
    "If rejected, Writer AI is revising the draft (up to 5 attempts)…",
  ];
  if (message) message.textContent = stages[stage];
  clearInterval(coverLetterProgressTimer);
  coverLetterProgressTimer = setInterval(() => {
    stage = (stage + 1) % stages.length;
    if (message) message.textContent = stages[stage];
  }, 2200);
}

function stopCoverLetterProgress() {
  clearInterval(coverLetterProgressTimer);
  coverLetterProgressTimer = null;
}

function populateCoverLetterContact(contact) {
  const fallback = profileContact();
  for (const [field, selector] of contactFields) {
    fillGlassInput($(selector), contact?.[field] || fallback[field] || "");
    clearFieldError(selector);
  }
}

function clearCoverLetterContact() {
  for (const [, selector] of contactFields) {
    fillGlassInput($(selector), "");
    clearFieldError(selector);
  }
}

// Modules load lazily. A user can select Upload Resume before this module has
// finished loading, so initialize from the radio's current state instead of
// assuming the default Profile option is still selected.
const letterResumeSource = setupProfileOrPdfResumeSource({
  sourceInputSelector: "input[name='cl-resume-source']",
  sourceControlSelector: '[data-candidate-source="cl"]',
  pdfSourceSelector: "#cl-pdf-source",
  additionalPdfSourceSelectors: ["#cl-contact-section"],
  fileInputSelector: "#cl-resume-pdf",
  dropzoneSelector: "#cl-dropzone",
  fileLabelSelector: "#cl-file-label",
  resumeTextSelector: "#cl-resume-text",
  onProfileLoaded: (context) => {
    coverLetterProfile = context.profile;
  },
  onExtract: (data) => populateCoverLetterContact(data.contact_information),
  onResetPdf: clearCoverLetterContact,
});

$("#btn-generate-cl")?.addEventListener("click", async () => {
  const resumeText = $("#cl-resume-text").value.trim();
  const jdText = $("#cl-jd-text").value.trim();
  if (!validateCoverLetterInputs()) return;

  const config = getAiConfigOrShowError();
  if (!config) return;

  $("#cl-brief").open = false;
  $("#cl-draft").hidden = false;
  $("#btn-generate-cl").disabled = true;
  $("#clear-cover-letter").disabled = true;
  $("#cl-loading").hidden = false;
  $("#cl-result-card").hidden = true;
  $("#cl-export-group").hidden = true;
  $("#cl-review-card").hidden = true;
  startCoverLetterProgress();

  try {
    const res = await fetchWithTimeout(
      "/api/v1/cover-letter/generate/stream",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          resume_text: resumeText,
          job_description: jdText,
          provider: config.provider,
          model_name: config.model_name,
          api_key: config.api_key,
          api_base: config.api_base || "",
          date_str: new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "long", day: "numeric" }).format(new Date()),
          user_info: {
            ...coverLetterUserInfo(),
          },
          job_title: $("#cl-job-title")?.value.trim() || "",
          company_name: $("#cl-company-name")?.value.trim() || "",
          company_location: $("#cl-company-location")?.value.trim() || "",
          hiring_manager: $("#cl-hiring-manager")?.value.trim() || "",
          company_information: $("#cl-company-info")?.value.trim() || "",
        }),
      },
      300000,
    );
    if (!res.ok || !res.body) {
      throw new Error(await responseErrorMessage(res, "Cover letter generation failed."));
    }

    const stageLabels = {
      writer: "Writer AI is drafting your cover letter…",
      writer_done: "Draft complete — Reviewer AI is checking every factual claim…",
      reviewer: "Reviewer AI is auditing factual grounding…",
    };
    const message = $("#cl-loading-message");
    let data = null;
    const handleEvent = (event) => {
      if (event.type === "stage" && message) {
        message.textContent = stageLabels[event.stage]
          ? `${stageLabels[event.stage]}${event.attempt ? ` (attempt ${event.attempt}/5)` : ""}`
          : message.textContent;
      } else if (event.type === "done") {
        data = event.result;
      }
    };

    for await (const event of readSseEvents(res)) handleEvent(event);
    if (!data) throw new Error("Generation ended without a result");
    if (!data.ok) throw new Error(data.error || "Generation failed");

    $("#cl-editor-text").value = data.text;
    const reviewCard = $("#cl-review-card");
    if (reviewCard) {
      const approved = data.review_approved === true;
      const rejected = data.review_approved === false;
      const attempts = data.review_attempts ? ` (${data.review_attempts}/5 attempts)` : "";
      reviewCard.hidden = !approved && !rejected;
      if (reviewCard.hidden) {
        showToast(data.review_summary || "The draft is ready. The reviewer could not complete its check.", { variant: "info" });
      } else {
        const title = approved ? `Reviewer approved the letter${attempts}` : `Reviewer requested revisions${attempts}`;
        const details = [...(data.review_unsupported_claims || []), ...(data.review_issues || [])];
        reviewCard.innerHTML = `<h3>${escapeHtml(title)}</h3><p>${escapeHtml(data.review_summary || "Review the letter before sending.")}</p>${details.length ? `<ul>${details.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}`;
      }
    }
    $("#cl-result-card").hidden = false;
    $("#cl-export-group").hidden = false;
    if (!$("#tab-cover-letter").hidden) $("#cl-editor-text").focus();
  } catch (err) {
    $("#cl-brief").open = true;
    $("#cl-draft").hidden = true;
    showErrorToast(err, { title: "Cover letter generation failed" });
  } finally {
    $("#btn-generate-cl").disabled = false;
    $("#clear-cover-letter").disabled = false;
    stopCoverLetterProgress();
    $("#cl-loading").hidden = true;
  }
});

const exportFeedback = Object.fromEntries(["docx", "pdf"].map(format => [format, createActionFeedback(`#btn-export-${format}`, {
  scope: "#cl-editor-text", pending: "Exporting…", success: "Download started", resetAfter: 2200,
})]));

async function downloadExport(format) {
  if ($(`#btn-export-${format}`).disabled) return;
  const bodyText = $("#cl-editor-text").value.trim();
  if (!bodyText) {
    showFieldError("#cl-editor-text", "Add letter text before exporting.");
    return;
  }
  const payload = {
    body: bodyText,
    user_info: {
      ...coverLetterUserInfo(),
    },
    format,
  };

  const finish = exportFeedback[format].begin();
  try {
    const res = await fetch("/api/v1/cover-letter/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(await responseErrorMessage(res, "The cover letter could not be exported."));
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `Cover_Letter.${format}`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    finish(true);
  } catch (err) {
    showErrorToast(err, { title: "Export failed" });
  } finally {
    finish(false);
  }
}

$("#btn-export-docx")?.addEventListener("click", () => downloadExport("docx"));
$("#btn-export-pdf")?.addEventListener("click", () => downloadExport("pdf"));

$("#clear-cover-letter")?.addEventListener("click", () => {
  letterResumeSource.resetToProfile();
  clearFieldError("#cl-jd-text");
  clearFieldError("#cl-editor-text");
  $("#cl-jd-text").value = "";
  $("#cl-editor-text").value = "";
  $("#cl-review-card").hidden = true;
  fillGlassInput($("#cl-job-title"), "");
  fillGlassInput($("#cl-company-name"), "");
  fillGlassInput($("#cl-company-location"), "");
  fillGlassInput($("#cl-hiring-manager"), "");
  $("#cl-company-info").value = "";
  $("#cl-brief").open = true;
  $("#cl-draft").hidden = true;
  $("#cl-loading").hidden = true;
  $("#cl-result-card").hidden = true;
  $("#cl-export-group").hidden = true;
});
