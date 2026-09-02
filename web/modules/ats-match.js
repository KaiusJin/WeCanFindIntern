import { $, escapeHtml, showErrorDialog } from "./helpers.js?v=20260901-error-dialog-minimal-v1";
import {
  createAtsCommentary,
  renderAtsBreakdown,
  requestAtsDiagnostic,
  setupAtsResumeSource,
} from "./ats-shared.js?v=20260902-shared-ats-v1";

const commentary = createAtsCommentary({
  defaultMessage: "Calculate the match to generate personalized feedback.",
  statusSelector: "#job-match-ai-status",
  resultSelector: "#job-match-ai-result",
  summarySelector: "#job-match-ai-summary",
  strengthsBlockSelector: "#job-match-ai-strengths-block",
  strengthsSelector: "#job-match-ai-strengths",
  improvementsSelector: "#job-match-ai-improvements",
});

function resetResult() {
  $("#ats-match-result").hidden = true;
  $("#ats-match-pending").hidden = false;
  commentary.invalidate();
}

function renderMatch(result) {
  $("#ats-match-pending").hidden = true;
  $("#ats-match-result").hidden = false;
  $("#ats-match-score").textContent = result.insufficient_evidence ? "—" : result.score;
  $("#ats-match-level").textContent = result.level;
  $("#ats-match-summary").textContent = result.summary;
  renderAtsBreakdown("#ats-match-breakdown", result.breakdown);
  $("#ats-match-evidence").innerHTML = (result.matched || [])
    .map((item) => `<span class="tag" title="${escapeHtml(item.resume_evidence || "")}">${escapeHtml(item.requirement)}</span>`)
    .join("") || '<span class="muted-copy">No direct requirement evidence matched.</span>';
  const gaps = [...(result.missing || []), ...(result.partial_matches || []), ...(result.unknowns || [])];
  $("#ats-match-gaps").innerHTML = gaps
    .map((item) => `<span class="tag" title="${escapeHtml(item.job_evidence || "")}">${escapeHtml(item.requirement)} · ${escapeHtml(item.status)}</span>`)
    .join("") || '<span class="muted-copy">No assessed gaps detected.</span>';
  const eligibility = result.eligibility_flags || [];
  $("#ats-eligibility-block").hidden = !eligibility.length;
  $("#ats-eligibility-flags").innerHTML = eligibility.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  $("#ats-match-suggestions").innerHTML = (
    result.suggestions?.length ? result.suggestions : ["Keep the resume evidence specific and truthful."]
  ).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
}

async function generateCommentary(resumeText, jobDescription, diagnostic) {
  await commentary.generate("/api/v1/ats/match/commentary", {
    resume_text: resumeText,
    job_description: jobDescription,
    diagnostic,
  });
}

const resumeSource = setupAtsResumeSource({
  fileInputSelector: "#ats-match-file-input",
  dropzoneSelector: "#ats-match-dropzone",
  fileLabelSelector: "#ats-match-file-label",
  resumeTextSelector: "#ats-match-resume-text",
  onTextChanged: resetResult,
});

$("#ats-match-jd-text")?.addEventListener("input", resetResult);

$("#clear-ats-match")?.addEventListener("click", () => {
  resumeSource.reset();
  $("#ats-match-jd-text").value = "";
  $("#ats-match-loading").hidden = true;
  resetResult();
});

$("#btn-run-ats-match")?.addEventListener("click", async () => {
  const resumeText = $("#ats-match-resume-text").value.trim();
  const jobDescription = $("#ats-match-jd-text").value.trim();
  if (!resumeText) {
    showErrorDialog("No resume content was provided.", {
      title: "Resume required",
      guidance: "Upload a resume PDF or paste your resume text, then calculate the match again.",
    });
    return;
  }
  if (!jobDescription) {
    showErrorDialog("The target job description is empty.", {
      title: "Job description required",
      guidance: "Paste the job description you want to compare against, then calculate the match again.",
    });
    return;
  }

  $("#ats-match-loading").hidden = false;
  $("#ats-match-pending").hidden = true;
  $("#ats-match-result").hidden = true;
  try {
    const result = await requestAtsDiagnostic(
      "/api/v1/ats/match",
      { resume_text: resumeText, job_description: jobDescription },
      "Job match calculation failed.",
    );
    renderMatch(result);
    await generateCommentary(resumeText, jobDescription, result);
  } catch (error) {
    resetResult();
    showErrorDialog(error, { title: "Job match calculation failed" });
  } finally {
    $("#ats-match-loading").hidden = true;
  }
});
