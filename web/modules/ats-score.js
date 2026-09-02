import { $, escapeHtml, showErrorDialog } from "./helpers.js?v=20260901-error-dialog-minimal-v1";
import {
  createAtsCommentary,
  renderAtsBreakdown,
  requestAtsDiagnostic,
  setupAtsResumeSource,
} from "./ats-shared.js?v=20260902-shared-ats-v1";

let uploadedReadiness = null;
let uploadedResumeText = "";
const commentary = createAtsCommentary({
  defaultMessage: "Calculate the score to generate personalized feedback.",
  statusSelector: "#ats-score-ai-status",
  resultSelector: "#ats-score-ai-result",
  summarySelector: "#ats-score-ai-summary",
  strengthsBlockSelector: "#ats-score-ai-strengths-block",
  strengthsSelector: "#ats-score-ai-strengths",
  improvementsSelector: "#ats-score-ai-improvements",
});

function resetResult() {
  $("#ats-readiness-result").hidden = true;
  $("#ats-readiness-pending").hidden = false;
  commentary.invalidate();
}

function renderScore(result) {
  $("#ats-readiness-pending").hidden = true;
  $("#ats-readiness-result").hidden = false;
  $("#ats-readiness-score").textContent = result.score;
  $("#ats-readiness-level").textContent = result.level;
  $("#ats-readiness-summary").textContent = result.summary;
  renderAtsBreakdown("#ats-readiness-breakdown", result.breakdown);
  $("#ats-readiness-issues").innerHTML = (
    result.issues?.length ? result.issues : ["No material parsing issues detected."]
  ).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  $("#ats-readiness-limitations").textContent = (result.limitations || []).join(" ");
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
  uploadedReadiness = null;
  uploadedResumeText = "";
  resumeSource.reset();
  $("#ats-score-loading").hidden = true;
  resetResult();
});

$("#btn-run-ats-score")?.addEventListener("click", async () => {
  const resumeText = $("#ats-score-resume-text").value.trim();
  if (!resumeText) {
    showErrorDialog("No resume content was provided.", {
      title: "Resume required",
      guidance: "Upload a resume PDF or paste your resume text, then calculate the score again.",
    });
    return;
  }

  $("#ats-score-loading").hidden = false;
  $("#ats-readiness-pending").hidden = true;
  $("#ats-readiness-result").hidden = true;
  try {
    const result = uploadedReadiness && resumeText === uploadedResumeText.trim()
      ? uploadedReadiness
      : await requestAtsDiagnostic(
        "/api/v1/ats/score",
        { resume_text: resumeText },
        "Resume ATS scoring failed.",
      );
    renderScore(result);
    await generateCommentary(resumeText, result);
  } catch (error) {
    resetResult();
    showErrorDialog(error, { title: "Resume ATS scoring failed" });
  } finally {
    $("#ats-score-loading").hidden = true;
  }
});
