import { $, escapeHtml, showErrorToast } from "./helpers.js?v=20260901-error-dialog-minimal-v1";
import {
  createAtsCommentary,
  renderAtsBreakdown,
  requestAtsDiagnostic,
  setupAtsResumeSource,
  validateAtsText,
} from "./ats-shared.js?v=20260902-shared-ats-v1";

let uploadedReadiness = null;
let uploadedResumeText = "";
let reportVersion = 0;
const commentary = createAtsCommentary({
  statusSelector: "#ats-score-ai-status",
  resultSelector: "#ats-score-ai-result",
  summarySelector: "#ats-score-ai-summary",
  strengthsBlockSelector: "#ats-score-ai-strengths-block",
  strengthsSelector: "#ats-score-ai-strengths",
  improvementsSelector: "#ats-score-ai-improvements",
});

function resetResult() {
  reportVersion += 1;
  $("#ats-score-source").open = true;
  $("#ats-score-loading").hidden = true;
  $("#ats-readiness-result").hidden = true;
  commentary.invalidate();
}

function renderScore(result) {
  $("#ats-readiness-result").hidden = false;
  $("#ats-readiness-score").textContent = result.score;
  $("#ats-readiness-level").textContent = result.level;
  $("#ats-readiness-summary").textContent = result.summary;
  $("#ats-readiness-method").textContent = [
    result.mode === "pdf_layout" ? "PDF layout analysis" : "Text analysis",
    result.confidence && `${result.confidence} confidence`,
  ].filter(Boolean).join(" · ");
  renderAtsBreakdown("#ats-readiness-breakdown", result.breakdown);
  $("#ats-readiness-issues").innerHTML = (
    result.issues?.length ? result.issues : ["No material parsing issues detected."]
  ).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  $("#ats-readiness-limitations").textContent = (result.limitations || []).join(" ");
  $("#ats-score-source").open = false;
  if (!$("#tab-ats-score").hidden) $("#ats-score-report-heading").focus();
}

async function generateCommentary(resumeText, diagnostic) {
  await commentary.generate("/api/v1/ats/score/commentary", {
    resume_text: resumeText,
    diagnostic,
  });
}

const resumeSource = setupAtsResumeSource({
  fileInputSelector: "#ats-score-file-input",
  dropzoneSelector: "#ats-score-dropzone",
  fileLabelSelector: "#ats-score-file-label",
  resumeTextSelector: "#ats-score-resume-text",
  onExtract: async (data) => {
    resetResult();
    uploadedReadiness = data.parsing_readiness;
    uploadedResumeText = data.text;
    renderScore(uploadedReadiness);
    await generateCommentary(uploadedResumeText, uploadedReadiness);
  },
  onTextChanged: () => {
    uploadedReadiness = null;
    uploadedResumeText = "";
    resetResult();
  },
});

$("#clear-ats-score")?.addEventListener("click", () => {
  resumeSource.reset();
});

$("#btn-run-ats-score")?.addEventListener("click", async () => {
  const resumeText = $("#ats-score-resume-text").value.trim();
  if (!validateAtsText("#ats-score-resume-text", resumeText, "Paste your resume text or upload a PDF.")) return;

  const version = ++reportVersion;
  $("#ats-score-loading").hidden = false;
  $("#ats-readiness-result").hidden = true;
  try {
    const result = uploadedReadiness && resumeText === uploadedResumeText.trim()
      ? uploadedReadiness
      : await requestAtsDiagnostic(
        "/api/v1/ats/score",
        { resume_text: resumeText },
        "Resume ATS scoring failed.",
      );
    if (version !== reportVersion) return;
    $("#ats-score-loading").hidden = true;
    renderScore(result);
    await generateCommentary(resumeText, result);
  } catch (error) {
    if (version !== reportVersion) return;
    resetResult();
    showErrorToast(error, { title: "Resume ATS scoring failed" });
  }
});
