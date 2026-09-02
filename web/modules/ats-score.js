import { $, escapeHtml, showErrorDialog } from "./helpers.js?v=20260901-error-dialog-minimal-v1";
import { validateAiConfig } from "./settings.js?v=20260902-settings-v2";
import {
  renderAtsBreakdown,
  requestAtsDiagnostic,
  setupAtsResumeSource,
} from "./ats-shared.js";

let uploadedReadiness = null;
let uploadedResumeText = "";
let commentaryRequestId = 0;

function resetCommentary(message = "Calculate the score to generate personalized feedback.") {
  $("#ats-score-ai-status").textContent = message;
  $("#ats-score-ai-status").hidden = false;
  $("#ats-score-ai-result").hidden = true;
}

function resetResult() {
  commentaryRequestId += 1;
  $("#ats-readiness-result").hidden = true;
  $("#ats-readiness-pending").hidden = false;
  resetCommentary();
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

function renderCommentary(commentary) {
  $("#ats-score-ai-status").hidden = true;
  $("#ats-score-ai-result").hidden = false;
  $("#ats-score-ai-summary").textContent = commentary.summary;
  const strengths = commentary.strengths || [];
  $("#ats-score-ai-strengths-block").hidden = !strengths.length;
  $("#ats-score-ai-strengths").innerHTML = strengths
    .map((item) => `<li>${escapeHtml(item)}</li>`)
    .join("");
  $("#ats-score-ai-improvements").innerHTML = (commentary.improvements || [])
    .map((item) => `<li>${escapeHtml(item)}</li>`)
    .join("");
}

async function generateCommentary(resumeText, diagnostic) {
  const requestId = ++commentaryRequestId;
  let config;
  try {
    config = validateAiConfig();
  } catch (error) {
    if (requestId === commentaryRequestId) resetCommentary(error.message);
    return;
  }

  resetCommentary("Generating personalized AI feedback…");
  try {
    const response = await requestAtsDiagnostic(
      "/api/v1/ats/score/commentary",
      {
        resume_text: resumeText,
        diagnostic,
        provider: config.provider,
        model_name: config.model_name,
        api_key: config.api_key,
        api_base: config.api_base || "",
      },
      "AI feedback could not be generated.",
    );
    if (requestId !== commentaryRequestId) return;
    if (!response.ok || !response.commentary) {
      resetCommentary(response.error || "AI feedback could not be generated right now.");
      return;
    }
    renderCommentary(response.commentary);
  } catch (error) {
    if (requestId === commentaryRequestId) {
      resetCommentary(error.message || "AI feedback could not be generated right now.");
    }
  }
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
