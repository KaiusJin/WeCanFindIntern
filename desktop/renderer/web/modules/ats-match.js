import { $, clearFieldError, escapeHtml, showErrorToast } from "./helpers.js?v=20260901-error-dialog-minimal-v1";
import {
  createAtsCommentary,
  renderAtsBreakdown,
  requestAtsDiagnostic,
  setupAtsResumeSource,
  validateAtsText,
} from "./ats-shared.js?v=20260902-shared-ats-v1";

let reportVersion = 0;
const commentary = createAtsCommentary({
  statusSelector: "#job-match-ai-status",
  resultSelector: "#job-match-ai-result",
  summarySelector: "#job-match-ai-summary",
  strengthsBlockSelector: "#job-match-ai-strengths-block",
  strengthsSelector: "#job-match-ai-strengths",
  improvementsSelector: "#job-match-ai-improvements",
});

function resetResult() {
  reportVersion += 1;
  $("#ats-match-source").open = true;
  $("#ats-match-loading").hidden = true;
  $("#ats-match-result").hidden = true;
  commentary.invalidate();
}

function renderMatch(result) {
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
  $("#ats-match-source").open = false;
  if (!$("#tab-ats-match").hidden) $("#ats-match-report-heading").focus();
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
  onExtract: resetResult,
  onTextChanged: resetResult,
});

$("#ats-match-jd-text")?.addEventListener("input", resetResult);

$("#clear-ats-match")?.addEventListener("click", () => {
  resumeSource.reset();
  $("#ats-match-jd-text").value = "";
  clearFieldError("#ats-match-jd-text");
});

$("#btn-run-ats-match")?.addEventListener("click", async () => {
  const resumeText = $("#ats-match-resume-text").value.trim();
  const jobDescription = $("#ats-match-jd-text").value.trim();
  const hasResume = validateAtsText("#ats-match-resume-text", resumeText, "Paste your resume text or upload a PDF.");
  const hasJob = validateAtsText("#ats-match-jd-text", jobDescription, "Paste the job description you want to compare.", hasResume);
  if (!hasResume || !hasJob) return;

  const version = ++reportVersion;
  $("#ats-match-loading").hidden = false;
  $("#ats-match-result").hidden = true;
  try {
    const result = await requestAtsDiagnostic(
      "/api/v1/ats/match",
      { resume_text: resumeText, job_description: jobDescription },
      "Job match calculation failed.",
    );
    if (version !== reportVersion) return;
    $("#ats-match-loading").hidden = true;
    renderMatch(result);
    await generateCommentary(resumeText, jobDescription, result);
  } catch (error) {
    if (version !== reportVersion) return;
    resetResult();
    showErrorToast(error, { title: "Job match calculation failed" });
  }
});
