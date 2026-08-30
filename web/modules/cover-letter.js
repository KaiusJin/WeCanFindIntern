import { $, escapeHtml, fetchWithTimeout, setupDropzone } from "./helpers.js";
import { validateAiConfig } from "./settings.js";

// =========================================================
// SECTION 4: COVER LETTER GENERATOR & EXPORT
// =========================================================

let coverLetterProfile = null;
let coverLetterProgressTimer = null;

export function profileToCoverLetterText(profile) {
  const lines = [];
  const basics = profile?.basics || {};
  if (basics.full_name) lines.push(basics.full_name);
  [basics.email, basics.phone, basics.linkedin_url, basics.github_url, basics.portfolio_url]
    .filter(Boolean).forEach((value) => lines.push(value));
  const append = (title, entries, formatter) => {
    if (!entries?.length) return;
    lines.push("", title);
    entries.forEach((entry) => lines.push(formatter(entry)));
  };
  append("Education", profile.education, (entry) => [entry.institution, entry.degree, entry.major, entry.specialization, entry.graduation_date_text].filter(Boolean).join(" | "));
  append("Work Experience", profile.work_experience, (entry) => [entry.title, entry.company, entry.start_date_text, entry.end_date_text, entry.description, ...(entry.skills || [])].filter(Boolean).join(" | "));
  append("Projects", profile.projects, (entry) => [entry.name, entry.description, entry.project_url, entry.github_url, ...(entry.skills || [])].filter(Boolean).join(" | "));
  append("Skills", profile.skills, (entry) => entry.name);
  append("Certifications", profile.certifications, (entry) => [entry.name, entry.issuer, entry.issue_date_text].filter(Boolean).join(" | "));
  append("Languages", profile.languages, (entry) => [entry.name, entry.proficiency].filter(Boolean).join(" | "));
  append("Awards", profile.awards, (entry) => [entry.title, entry.issuer, entry.date_text, entry.description].filter(Boolean).join(" | "));
  return lines.join("\n").trim();
}

async function loadCoverLetterProfile() {
  const status = $("#cl-profile-source-status");
  try {
    const response = await fetch("/api/v1/profile");
    if (!response.ok) throw new Error("Could not load Profile.");
    const profile = await response.json();
    coverLetterProfile = profile;
    const text = profileToCoverLetterText(profile);
    $("#cl-resume-text").value = text;
    if (status) {
      status.textContent = text ? "Using saved Profile as candidate context" : "Your Profile is empty. Add Profile data or upload a resume.";
    }
  } catch (error) {
    if (status) status.textContent = error.message;
    $("#cl-resume-text").value = "";
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
  if (isPdf && !$("#cl-resume-pdf")?.files?.length) {
    alert("Upload a resume PDF before generating the cover letter.");
    return false;
  }
  if (!resumeText) {
    alert(isPdf ? "Extract your resume PDF before generating the cover letter." : "Your Profile has no resume content yet.");
    return false;
  }
  if (!jdText) {
    alert("Enter a Target Job Description before generating the cover letter.");
    return false;
  }
  const contact = coverLetterUserInfo();
  const missing = [["full_name", "full name"], ["email", "email"], ["phone", "phone"], ["linkedin", "LinkedIn or portfolio"]]
    .filter(([field]) => !contact[field]);
  if (missing.length) {
    const location = isPdf ? "the Contact Details form" : "your Profile";
    alert(`Complete ${missing.map(([, label]) => label).join(", ")} in ${location} before generating the cover letter.`);
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
  const form = new FormData();
  form.append("file", file);
  try {
    const response = await fetch("/api/v1/ats/extract-pdf", { method: "POST", body: form });
    const result = await response.json();
    if (!result.ok) throw new Error(result.error || "Resume extraction failed.");
    $("#cl-resume-text").value = result.text;
    if (label) label.textContent = `✓ Extracted from ${file.name}`;
  } catch (error) {
    if (label) label.textContent = `Upload error: ${error.message}`;
    $("#cl-resume-text").value = "";
  }
}

document.querySelectorAll("input[name='cl-resume-source']").forEach((input) => input.addEventListener("change", (event) => {
  const isPdf = event.target.value === "pdf";
  $("#cl-pdf-source").hidden = !isPdf;
  $("#cl-contact-section").hidden = !isPdf;
  if (isPdf) {
    $("#cl-resume-text").value = "";
    $("#cl-file-label").textContent = "Click or drag & drop resume PDF";
  } else loadCoverLetterProfile();
}));
$("#cl-resume-pdf")?.addEventListener("change", (event) => extractCoverLetterPdf(event.target.files?.[0]));
setupDropzone($("#cl-dropzone"), (files) => extractCoverLetterPdf(files[0]));
loadCoverLetterProfile();

$("#btn-generate-cl")?.addEventListener("click", async () => {
  const resumeText = $("#cl-resume-text").value.trim();
  const jdText = $("#cl-jd-text").value.trim();
  if (!validateCoverLetterInputs()) return;

  let config;
  try {
    config = validateAiConfig();
  } catch (err) {
    alert(err.message);
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
      const errText = await res.text();
      let msg = errText;
      try {
        const json = JSON.parse(errText);
        if (json.detail) msg = json.detail;
        else if (json.error) msg = json.error;
      } catch { }
      throw new Error(msg || `Server error (${res.status})`);
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

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop();
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data: ")) continue;
        try {
          handleEvent(JSON.parse(line.slice(6)));
        } catch (_) { }
      }
    }
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
    stopCoverLetterProgress();
    $("#cl-loading").hidden = true;
    $("#cl-result-card").hidden = false;
    $("#cl-export-group").hidden = false;
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
    if (!res.ok) throw new Error("Export failed");
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
    alert(`Export download error: ${err.message}`);
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
