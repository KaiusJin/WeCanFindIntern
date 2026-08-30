import { $, escapeHtml, fetchWithTimeout, responseErrorMessage, setupDropzone, showErrorDialog } from "./helpers.js";

let uploadedReadiness = null;
let uploadedResumeText = "";

function renderBreakdown(selector, items = []) {
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

function renderReadiness(result) {
  if (!result) return;
  $("#ats-readiness-pending").hidden = true;
  $("#ats-readiness-result").hidden = false;
  $("#ats-readiness-score").textContent = result.score;
  $("#ats-readiness-level").textContent = `${result.level} · ${result.confidence} confidence`;
  $("#ats-readiness-mode").textContent = result.mode === "pdf_layout" ? "PDF layout" : "Text only";
  $("#ats-readiness-summary").textContent = result.summary;
  renderBreakdown("#ats-readiness-breakdown", result.breakdown);
  $("#ats-readiness-issues").innerHTML = (result.issues?.length ? result.issues : ["No material parsing issues detected."])
    .map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  $("#ats-readiness-limitations").textContent = (result.limitations || []).join(" ");
}

function renderJobMatch(result) {
  if (!result) return;
  $("#ats-match-pending").hidden = true;
  $("#ats-match-result").hidden = false;
  $("#ats-match-mode").textContent = "Deterministic v1";
  $("#ats-match-score").textContent = result.insufficient_evidence ? "—" : result.score;
  $("#ats-match-level").textContent = `${result.level} · ${result.confidence} confidence`;
  $("#ats-match-summary").textContent = result.summary;
  renderBreakdown("#ats-match-breakdown", result.breakdown);
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
  $("#ats-match-suggestions").innerHTML = (result.suggestions?.length ? result.suggestions : ["Keep the resume evidence specific and truthful."])
    .map((item) => `<li>${escapeHtml(item)}</li>`).join("");
}

async function extractAtsFile(file) {
  if (!file) return;
  $("#ats-file-label").textContent = `Selected: ${file.name}`;
  const formData = new FormData();
  formData.append("file", file);
  try {
    $("#ats-file-label").textContent = `Extracting and checking ${file.name}…`;
    const res = await fetch("/api/v1/ats/extract-pdf", { method: "POST", body: formData });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok || !data.text) {
      throw new Error(data.detail || data.error || "The resume could not be read.");
    }
    $("#ats-resume-text").value = data.text;
    uploadedResumeText = data.text;
    $("#ats-file-label").textContent = `✓ Extracted from ${file.name}`;
    uploadedReadiness = data.parsing_readiness || null;
    renderReadiness(uploadedReadiness);
  } catch (err) {
    uploadedReadiness = null;
    $("#ats-file-label").textContent = "Click or drag & drop resume PDF";
    showErrorDialog(err, { title: "Resume upload failed" });
  }
}

const atsFileInput = $("#ats-file-input");
atsFileInput?.addEventListener("change", (event) => extractAtsFile(event.target.files?.[0]));
setupDropzone($("#ats-dropzone"), (files) => extractAtsFile(files[0]));
$("#ats-resume-text")?.addEventListener("input", (event) => {
  if (event.target.value === uploadedResumeText) return;
  uploadedReadiness = null;
  $("#ats-readiness-result").hidden = true;
  $("#ats-readiness-pending").hidden = false;
  $("#ats-readiness-mode").textContent = "Waiting for PDF";
});

$("#clear-ats")?.addEventListener("click", () => {
  uploadedReadiness = null;
  uploadedResumeText = "";
  $("#ats-resume-text").value = "";
  $("#ats-jd-text").value = "";
  $("#ats-file-input").value = "";
  $("#ats-file-label").textContent = "Click or drag & drop resume PDF";
  $("#ats-loading").hidden = true;
  $("#ats-readiness-result").hidden = true;
  $("#ats-readiness-pending").hidden = false;
  $("#ats-readiness-mode").textContent = "Waiting for PDF";
  $("#ats-match-result").hidden = true;
  $("#ats-match-pending").hidden = false;
  $("#ats-match-mode").textContent = "Waiting for job";
});

$("#btn-run-ats")?.addEventListener("click", async () => {
  const resumeText = $("#ats-resume-text").value.trim();
  const jdText = $("#ats-jd-text").value.trim();
  if (!resumeText) {
    showErrorDialog("No resume content was provided.", { title: "Resume required", guidance: "Upload a resume PDF or paste your resume text, then run the evaluation again." });
    return;
  }
  if (!jdText) {
    showErrorDialog("The target job description is empty.", { title: "Job description required", guidance: "Paste the job description you want to compare against, then try again." });
    return;
  }

  $("#ats-loading").hidden = false;

  try {
    const res = await fetchWithTimeout(
      "/api/v1/ats/review",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resume_text: resumeText, job_description: jdText }),
      },
      30000,
    );
    if (!res.ok) throw new Error(await responseErrorMessage(res, "ATS evaluation failed."));
    const data = await res.json();
    if (!data.ok || !data.job_match) throw new Error(data.error || "ATS analysis failed");
    renderReadiness(uploadedReadiness || data.parsing_readiness);
    renderJobMatch(data.job_match);
  } catch (err) {
    showErrorDialog(err, { title: "ATS evaluation failed" });
  } finally {
    $("#ats-loading").hidden = true;
  }
});
