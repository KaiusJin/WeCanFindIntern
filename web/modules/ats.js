import { $, fetchWithTimeout, setupDropzone } from "./helpers.js";
import { validateAiConfig } from "./settings.js";

// =========================================================
// SECTION 2: ATS RESUME REVIEW
// =========================================================

async function extractAtsFile(file) {
  if (!file) return;
  $("#ats-file-label").textContent = `Selected: ${file.name}`;
  const formData = new FormData();
  formData.append("file", file);
  try {
    $("#ats-file-label").textContent = `Extracting text from ${file.name}…`;
    const res = await fetch("/api/v1/ats/extract-pdf", { method: "POST", body: formData });
    const data = await res.json();
    if (data.ok && data.text) {
      $("#ats-resume-text").value = data.text;
      $("#ats-file-label").textContent = `✓ Extracted from ${file.name}`;
    } else {
      $("#ats-file-label").textContent = `Extraction failed: ${data.error || "Unknown"}`;
    }
  } catch (err) {
    $("#ats-file-label").textContent = `Upload error: ${err.message}`;
  }
}

const atsFileInput = $("#ats-file-input");
if (atsFileInput) {
  atsFileInput.addEventListener("change", (e) => extractAtsFile(e.target.files?.[0]));
}
setupDropzone($("#ats-dropzone"), (files) => extractAtsFile(files[0]));

$("#clear-ats")?.addEventListener("click", () => {
  $("#ats-resume-text").value = "";
  $("#ats-jd-text").value = "";
  $("#ats-file-label").textContent = "Click or drag & drop resume PDF";
  $("#ats-empty").hidden = false;
  $("#ats-loading").hidden = true;
  $("#ats-result-card").hidden = true;
});

$("#btn-run-ats")?.addEventListener("click", async () => {
  const resumeText = $("#ats-resume-text").value.trim();
  const jdText = $("#ats-jd-text").value.trim();
  if (!resumeText) {
    alert("Please upload your resume PDF or paste your resume text first.");
    return;
  }
  if (!jdText) {
    alert("Please enter a target job description.");
    return;
  }

  let config;
  try {
    config = validateAiConfig();
  } catch (err) {
    alert(err.message);
    return;
  }

  $("#ats-empty").hidden = true;
  $("#ats-loading").hidden = false;
  $("#ats-result-card").hidden = true;

  try {
    const res = await fetchWithTimeout(
      "/api/v1/ats/review",
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
        }),
      },
      120000,
    );
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "ATS analysis failed");

    $("#ats-score-num").textContent = data.score;
    $("#ats-level-pill").textContent = data.level;
    $("#ats-summary-text").textContent = data.summary;

    const strengthsWrap = $("#ats-strengths-wrap");
    strengthsWrap.innerHTML = (data.strengths || []).map((s) => `<span class="tag">${escapeHtml(s)}</span>`).join("");

    const gapsWrap = $("#ats-gaps-wrap");
    gapsWrap.innerHTML = (data.gaps || []).map((g) => `<span class="tag">${escapeHtml(g)}</span>`).join("");

    const suggestionsWrap = $("#ats-suggestions-wrap");
    suggestionsWrap.innerHTML = (data.suggestions || []).map((s) => `<li>${escapeHtml(s)}</li>`).join("");

    $("#ats-loading").hidden = true;
    $("#ats-result-card").hidden = false;
  } catch (err) {
    $("#ats-loading").hidden = true;
    $("#ats-empty").hidden = false;
    alert(`ATS Evaluation Error: ${err.message}`);
  }
});
