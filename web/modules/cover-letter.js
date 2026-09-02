import { $, escapeHtml, fetchWithTimeout, responseErrorMessage, setupDropzone, showErrorDialog } from "./helpers.js?v=20260901-error-dialog-minimal-v1";
import { validateAiConfig } from "./settings.js?v=20260901-settings-v1";
import { extractResumePdf, loadProfileContext } from "./resume-source.js";
import { readSseEvents } from "./sse.js";

// =========================================================
// SECTION 4: COVER LETTER GENERATOR & EXPORT
// =========================================================

let coverLetterProfile = null;
let coverLetterProgressTimer = null;

async function loadCoverLetterProfile() {
  const status = $("#cl-profile-source-status");
  try {
    const context = await loadProfileContext();
    coverLetterProfile = context.profile;
    const text = context.resume_text || "";
    $("#cl-resume-text").value = text;
    if (status) {
      status.textContent = text ? "Using saved Profile as candidate context" : "Your Profile is empty. Add Profile data or upload a resume.";
    }
  } catch (error) {
    if (status) status.textContent = "Profile could not be loaded.";
    $("#cl-resume-text").value = "";
    showErrorDialog(error, { title: "Could not load Profile" });
  }
}

function coverLetterUserInfo() {
  const basics = coverLetterProfile?.basics || {};
  const usePdf = $("input[name='cl-resume-source'][value='pdf']")?.checked;
  const value = (id, fallback) => usePdf ? ($(`#${id}`)?.value.trim() || fallback) : fallback;
  return {
    full_name: value("cl-full-name", basics.full_name || ""),
    email: value("cl-email", basics.email || ""),
    phone: value("cl-phone", basics.phone || ""),
    linkedin: value("cl-linkedin", basics.linkedin_url || basics.github_url || basics.portfolio_url || ""),
    address: [basics.city, basics.region, basics.country].filter(Boolean).join(", "),
  };
}

function validateCoverLetterInputs() {
  const isPdf = $("input[name='cl-resume-source'][value='pdf']")?.checked;
  const resumeText = $("#cl-resume-text").value.trim();
  const jdText = $("#cl-jd-text").value.trim();
  if (!resumeText) {
    showErrorDialog(
      isPdf ? "No extracted resume content is available." : "Your Profile does not contain resume content.",
      { title: "Resume information required", guidance: isPdf ? "Upload a readable resume PDF, wait for extraction to finish, and try again." : "Add information to Profile or switch to Upload Resume, then try again." },
    );
    return false;
  }
  if (!jdText) {
    showErrorDialog("The Target Job Description is empty.", { title: "Job description required", guidance: "Paste the target role's job description, then generate the cover letter again." });
    return false;
  }
  const contact = coverLetterUserInfo();
  const missing = [["full_name", "full name"], ["email", "email"], ["phone", "phone"], ["linkedin", "LinkedIn or portfolio"]]
    .filter(([field]) => !contact[field]);
  if (missing.length) {
    const location = isPdf ? "the Contact Details form" : "your Profile";
    showErrorDialog(`Missing required contact details: ${missing.map(([, label]) => label).join(", ")}.`, { title: "Contact details incomplete", guidance: `Complete these fields in ${location}, then try again.` });
    return false;
  }
  return true;
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

async function extractCoverLetterPdf(file) {
  if (!file) return;
  const label = $("#cl-file-label");
  if (label) label.textContent = `Extracting from ${file.name}…`;
  try {
    const result = await extractResumePdf(file);
    $("#cl-resume-text").value = result.text;
    populateCoverLetterContact(result.contact_information);
    if (label) label.textContent = `✓ Extracted from ${file.name}`;
  } catch (error) {
    if (label) label.textContent = "Click or drag & drop resume PDF";
    $("#cl-resume-text").value = "";
    showErrorDialog(error, { title: "Resume upload failed" });
  }
}

function populateCoverLetterContact(contact) {
  if (!contact) return;
  const fields = {
    "cl-full-name": contact.full_name,
    "cl-email": contact.email,
    "cl-phone": contact.phone,
    "cl-linkedin": contact.linkedin,
  };
  Object.entries(fields).forEach(([id, value]) => {
    const input = $(`#${id}`);
    if (input) input.value = value || "";
  });
}

function clearCoverLetterContact() {
  ["cl-full-name", "cl-email", "cl-phone", "cl-linkedin"].forEach((id) => {
    const input = $(`#${id}`);
    if (input) input.value = "";
  });
}

function syncCoverLetterResumeSource({ resetPdf = false } = {}) {
  const isPdf = $("input[name='cl-resume-source'][value='pdf']")?.checked ?? false;
  $("#cl-pdf-source").hidden = !isPdf;
  $("#cl-contact-section").hidden = !isPdf;
  if (isPdf) {
    if (resetPdf) {
      $("#cl-resume-text").value = "";
      $("#cl-file-label").textContent = "Click or drag & drop resume PDF";
      clearCoverLetterContact();
    }
  } else loadCoverLetterProfile();
}

document.querySelectorAll("input[name='cl-resume-source']").forEach((input) => input.addEventListener("change", () => {
  syncCoverLetterResumeSource({ resetPdf: true });
}));
$("#cl-resume-pdf")?.addEventListener("change", (event) => extractCoverLetterPdf(event.target.files?.[0]));
setupDropzone($("#cl-dropzone"), (files) => extractCoverLetterPdf(files[0]));
// Modules load lazily. A user can select Upload Resume before this module has
// finished loading, so initialize from the radio's current state instead of
// assuming the default Profile option is still selected.
syncCoverLetterResumeSource({ resetPdf: true });

$("#btn-generate-cl")?.addEventListener("click", async () => {
  const resumeText = $("#cl-resume-text").value.trim();
  const jdText = $("#cl-jd-text").value.trim();
  if (!validateCoverLetterInputs()) return;

  let config;
  try {
    config = validateAiConfig();
  } catch (err) {
    showErrorDialog(err, { title: "AI settings required" });
    return;
  }

  $("#cl-empty").hidden = true;
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
      const title = approved ? `Reviewer AI approved the letter${attempts}` : rejected ? `Reviewer AI rejected the final draft${attempts}` : "Reviewer AI could not complete the check";
      const details = [...(data.review_unsupported_claims || []), ...(data.review_issues || [])];
      reviewCard.hidden = false;
      reviewCard.className = `cl-review-card ${approved ? "approved" : rejected ? "warning" : "unavailable"}`;
      reviewCard.innerHTML = `<strong>${title}</strong><span>${escapeHtml(data.review_summary || "Review the letter before sending.")}</span>${details.length ? `<ul>${details.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}`;
    }
    $("#cl-result-card").hidden = false;
    $("#cl-export-group").hidden = false;
  } catch (err) {
    $("#cl-empty").hidden = false;
    showErrorDialog(err, { title: "Cover letter generation failed" });
  } finally {
    stopCoverLetterProgress();
    $("#cl-loading").hidden = true;
  }
});

async function downloadExport(format) {
  const bodyText = $("#cl-editor-text").value.trim();
  if (!bodyText) return;
  const payload = {
    body: bodyText,
    user_info: {
      ...coverLetterUserInfo(),
    },
    format,
  };

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
  } catch (err) {
    showErrorDialog(err, { title: "Export failed" });
  }
}

$("#btn-export-docx")?.addEventListener("click", () => downloadExport("docx"));
$("#btn-export-pdf")?.addEventListener("click", () => downloadExport("pdf"));

$("#clear-cover-letter")?.addEventListener("click", () => {
  const profileRadio = $("input[name='cl-resume-source'][value='profile']");
  if (profileRadio) profileRadio.checked = true;
  $("#cl-pdf-source").hidden = true;
  $("#cl-contact-section").hidden = true;
  const fileInput = $("#cl-resume-pdf");
  if (fileInput) fileInput.value = "";
  const fileLabel = $("#cl-file-label");
  if (fileLabel) fileLabel.textContent = "Click or drag & drop resume PDF";
  $("#cl-resume-text").value = "";
  $("#cl-jd-text").value = "";
  $("#cl-editor-text").value = "";
  $("#cl-review-card").hidden = true;
  $("#cl-full-name").value = "";
  $("#cl-email").value = "";
  $("#cl-phone").value = "";
  $("#cl-linkedin").value = "";
  $("#cl-job-title").value = "";
  $("#cl-company-name").value = "";
  $("#cl-company-location").value = "";
  $("#cl-hiring-manager").value = "";
  $("#cl-company-info").value = "";
  $("#cl-empty").hidden = false;
  $("#cl-loading").hidden = true;
  $("#cl-result-card").hidden = true;
  $("#cl-export-group").hidden = true;
  loadCoverLetterProfile();
});
