import { $, escapeHtml, fetchWithTimeout, setupDropzone } from "./helpers.js";
import { validateAiConfig } from "./settings.js";
import { profileToCoverLetterText as profileToResumeText } from "./cover-letter.js";

// =========================================================
// SECTION 3: AI INTERVIEW COACH & AUDIO RECORDER
// =========================================================

let interviewProfile = null;

const interviewState = {
  questions: [],
  currentIndex: 0,
  sessionId: null,
  answered: new Set(),
  mediaRecorder: null,
  recordedChunks: [],
  recordedBlob: null,
  stream: null,
  timerInterval: null,
  secondsElapsed: 0,
};

// Compact stepper labels keyed by question category.
const STEP_LABELS = {
  intro: "Self Intro",
  experience: "Work Experience",
  experience_followup: "Experience Probe",
  project: "Project Deep-Dive",
  project_followup: "Project Probe",
};

function stepLabel(question, index) {
  return (
    STEP_LABELS[question.category]
    || (question.category_label || `Q${index + 1}`).replace(/^\d+\.\s*/, "")
  );
}

function renderActiveQuestion(index) {
  if (!interviewState.questions[index]) return;
  interviewState.currentIndex = index;
  const q = interviewState.questions[index];
  $("#interview-q-category").textContent = q.category_label || `Question ${index + 1}`;
  $("#interview-q-text").textContent = q.question;
  renderStepper();
  $("#interview-report-card").hidden = true;
  $("#interview-answer-text").value = "";
}

function renderStepper() {
  const stepper = $("#interview-stepper");
  if (!stepper) return;
  stepper.innerHTML = interviewState.questions
    .map((question, idx) => {
      const done = interviewState.answered.has(idx);
      const state = idx === interviewState.currentIndex ? " active" : done ? " done" : "";
      const num = done ? "✓" : idx + 1;
      return `<button class="step-badge${state}" data-q="${idx}" type="button" title="${escapeHtml(question.question)}"><span class="step-num">${num}</span>${escapeHtml(stepLabel(question, idx))}</button>`;
    })
    .join("");
  stepper.querySelectorAll(".step-badge").forEach((badge) => {
    badge.addEventListener("click", () => renderActiveQuestion(Number(badge.dataset.q)));
  });
}

// Candidate source: saved Profile or uploaded resume PDF (same flow as the
// Cover Letter section).
async function loadInterviewProfile() {
  const status = $("#int-profile-source-status");
  try {
    const response = await fetch("/api/v1/profile");
    if (!response.ok) throw new Error("Could not load Profile.");
    interviewProfile = await response.json();
    const text = profileToResumeText(interviewProfile);
    $("#int-resume-text").value = text;
    if (status) {
      status.textContent = text
        ? "Using saved Profile as candidate context"
        : "Your Profile is empty. Add Profile data or upload a resume.";
    }
  } catch (error) {
    if (status) status.textContent = error.message;
    $("#int-resume-text").value = "";
  }
}

function isPdfResumeSource() {
  return $("input[name='int-resume-source'][value='pdf']")?.checked ?? false;
}

function currentResumeText() {
  return $("#int-resume-text").value.trim();
}

async function extractInterviewPdf(file) {
  if (!file) return;
  const label = $("#int-file-label");
  if (label) label.textContent = `Extracting from ${file.name}…`;
  const form = new FormData();
  form.append("file", file);
  try {
    const response = await fetch("/api/v1/ats/extract-pdf", { method: "POST", body: form });
    const result = await response.json();
    if (!result.ok) throw new Error(result.error || "Resume extraction failed.");
    $("#int-resume-text").value = result.text;
    if (label) label.textContent = `✓ Extracted from ${file.name}`;
  } catch (error) {
    if (label) label.textContent = `Upload error: ${error.message}`;
    $("#int-resume-text").value = "";
  }
}

document.querySelectorAll("input[name='int-resume-source']").forEach((input) => input.addEventListener("change", (event) => {
  const isPdf = event.target.value === "pdf";
  $("#int-pdf-source").hidden = !isPdf;
  if (isPdf) {
    $("#int-resume-text").value = "";
    $("#int-file-label").textContent = "Click or drag & drop resume PDF";
  } else loadInterviewProfile();
}));
$("#int-resume-pdf")?.addEventListener("change", (event) => extractInterviewPdf(event.target.files?.[0]));
setupDropzone($("#int-dropzone"), (files) => extractInterviewPdf(files[0]));

$("#btn-generate-questions")?.addEventListener("click", async () => {
  const jdText = $("#interview-jd-text").value.trim();
  if (!jdText) {
    alert("Please enter a job description to generate interview questions.");
    return;
  }
  if (isPdfResumeSource() && !$("#int-resume-pdf")?.files?.length && !currentResumeText()) {
    alert("Upload a resume PDF before generating interview questions.");
    return;
  }
  if (!currentResumeText()) {
    alert("Your Profile has no resume content yet. Add Profile data or upload a resume.");
    return;
  }

  let config;
  try {
    config = validateAiConfig();
  } catch (err) {
    alert(err.message);
    return;
  }

  $("#interview-empty").hidden = true;
  $("#interview-loading").hidden = false;
  $("#interview-active-card").hidden = true;
  $("#interview-report-card").hidden = true;

  try {
    const res = await fetchWithTimeout(
      "/api/v1/interview/sessions",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          job_description: jdText,
          resume_text: currentResumeText(),
          provider: config.provider,
          model_name: config.model_name,
          api_key: config.api_key,
          api_base: config.api_base || "",
        }),
      },
      120000,
    );
    const data = await res.json();
    if (!res.ok || !data.questions?.length) {
      throw new Error(data.detail || data.error || "Failed to generate questions");
    }

    interviewState.questions = data.questions;
    interviewState.sessionId = data.session_id || null;
    interviewState.answered = new Set();
    renderActiveQuestion(0);

    $("#interview-loading").hidden = true;
    $("#interview-active-card").hidden = false;
  } catch (err) {
    $("#interview-loading").hidden = true;
    $("#interview-empty").hidden = false;
    alert(`Question generation failed: ${err.message}`);
  }
});

// Microphone Recording (audio only; transcription happens locally on the server)
async function initMicrophone() {
  if (interviewState.stream) return interviewState.stream;
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  interviewState.stream = stream;
  return stream;
}

function setRecordStatus(text) {
  const el = $("#recording-inline-status");
  if (el) {
    el.textContent = text;
    el.hidden = false;
  }
}

$("#btn-start-record")?.addEventListener("click", async () => {
  try {
    const stream = await initMicrophone();
    interviewState.recordedChunks = [];
    const mimeType = MediaRecorder.isTypeSupported("audio/webm")
      ? "audio/webm"
      : "";
    interviewState.mediaRecorder = new MediaRecorder(
      stream,
      mimeType ? { mimeType } : undefined,
    );
    interviewState.mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) interviewState.recordedChunks.push(e.data);
    };
    interviewState.mediaRecorder.onstop = () => {
      interviewState.recordedBlob = new Blob(interviewState.recordedChunks, {
        type: mimeType || "audio/webm",
      });
      stream.getTracks().forEach((t) => t.stop());
      interviewState.stream = null;
      setRecordStatus("Recording saved — analyze when ready, or re-record.");
    };
    interviewState.mediaRecorder.start();

    setRecordStatus("Recording — speak your answer, then stop.");
    $("#btn-start-record").hidden = true;
    $("#btn-stop-record").hidden = false;
    $("#recording-timer").hidden = false;

    interviewState.secondsElapsed = 0;
    $("#recording-time-text").textContent = "00:00";
    interviewState.timerInterval = setInterval(() => {
      interviewState.secondsElapsed += 1;
      const m = String(Math.floor(interviewState.secondsElapsed / 60)).padStart(2, "0");
      const s = String(interviewState.secondsElapsed % 60).padStart(2, "0");
      $("#recording-time-text").textContent = `${m}:${s}`;
    }, 1000);
  } catch (err) {
    setRecordStatus(`Microphone unavailable (${err.message}) — type your answer instead.`);
  }
});

$("#btn-stop-record")?.addEventListener("click", () => {
  if (interviewState.mediaRecorder && interviewState.mediaRecorder.state !== "inactive") {
    interviewState.mediaRecorder.stop();
  }
  clearInterval(interviewState.timerInterval);
  $("#recording-timer").hidden = true;
  $("#btn-start-record").hidden = false;
  $("#btn-stop-record").hidden = true;
  $("#btn-start-record").textContent = "↺ Re-record";
});

$("#btn-analyze-answer")?.addEventListener("click", async () => {
  const jdText = $("#interview-jd-text").value.trim();
  const q = interviewState.questions[interviewState.currentIndex];
  const answerText = $("#interview-answer-text").value.trim();

  if (!jdText) {
    alert("Missing job description.");
    return;
  }
  if (!answerText && !interviewState.recordedBlob) {
    alert("Record an audio answer or type your answer first.");
    return;
  }

  let config;
  try {
    config = validateAiConfig();
  } catch (err) {
    alert(err.message);
    return;
  }

  const formData = new FormData();
  formData.append("job_description", jdText);
  formData.append("question_context", q?.question || "");
  formData.append("answer_text", answerText);
  formData.append("provider", config.provider);
  formData.append("model_name", config.model_name || "");
  formData.append("api_key", config.api_key || "");
  formData.append("api_base", config.api_base || "");

  if (interviewState.recordedBlob) {
    formData.append("audio_file", interviewState.recordedBlob, "answer.webm");
  }
  if (interviewState.sessionId) {
    formData.append("session_id", interviewState.sessionId);
    formData.append("question_index", String(interviewState.currentIndex));
  }
  if (q?.eval_criteria?.length) {
    formData.append(
      "question_criteria",
      q.eval_criteria.map((c) => `- ${c}`).join("\n"),
    );
  }

  const btn = $("#btn-analyze-answer");
  btn.disabled = true;
  btn.textContent = answerText
    ? "Analyzing Performance with AI…"
    : "Transcribing audio locally, then analyzing…";

  try {
    const res = await fetchWithTimeout("/api/v1/interview/analyze", { method: "POST", body: formData }, 180000);
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Analysis failed");

    const interviewScore = Number(data.score) || 0;
    $("#interview-score-num").textContent = interviewScore;
    $("#interview-level-pill").textContent = interviewScore >= 80 ? "Strong Performance" : interviewScore >= 60 ? "Solid Performance" : "Needs Practice";
    $("#interview-summary-text").textContent = data.summary;

    if (data.star_feedback) {
      $("#star-feedback-block").hidden = false;
      $("#star-feedback-text").textContent = data.star_feedback;
    } else {
      $("#star-feedback-block").hidden = true;
    }

    const transcriptBlock = $("#transcript-block");
    if (data.transcript) {
      transcriptBlock.hidden = false;
      const duration = Number(data.answer_duration_seconds) || 0;
      const lang = data.transcript_language ? ` (${data.transcript_language})` : "";
      const durationNote = duration ? ` — ${Math.round(duration)}s` : "";
      $("#interview-transcript-text").textContent =
        `${data.transcript}${lang}${durationNote}`;
      if (!answerText) {
        // Surface the locally transcribed answer so the user can correct it
        // for the next round.
        $("#interview-answer-text").value = data.transcript;
      }
    } else {
      transcriptBlock.hidden = true;
    }

    const criteriaBlock = $("#criteria-results-block");
    const criteriaList = $("#interview-criteria-wrap");
    if (criteriaBlock && criteriaList) {
      const results = data.criteria_results || [];
      if (results.length) {
        criteriaBlock.hidden = false;
        const verdictIcon = { met: "✓", partial: "◐", missed: "✕" };
        criteriaList.innerHTML = results.map((result) => `
          <div class="criterion-result criterion-${escapeHtml(result.verdict || "missed")}">
            <span class="criterion-verdict">${verdictIcon[result.verdict] || "✕"}</span>
            <span class="criterion-text"><strong>${escapeHtml(result.criterion || "")}</strong>${result.note ? ` — ${escapeHtml(result.note)}` : ""}</span>
          </div>
        `).join("");
      } else {
        criteriaBlock.hidden = true;
      }
    }

    const timelineWrap = $("#interview-timeline-wrap");
    timelineWrap.innerHTML = (data.timeline || []).map((t) => {
      const section = t.section || t.type || "Observation";
      return `
      <div class="timeline-event-item">
        <span class="timeline-ts">${escapeHtml(section)}</span>
        <span class="timeline-obs">${escapeHtml(t.observation)}</span>
      </div>
    `;
    }).join("") || "<p class='detail-description'>No specific answer-phase notes.</p>";

    const adviceWrap = $("#interview-advice-wrap");
    adviceWrap.innerHTML = (data.advice || []).map((a) => `<li>${escapeHtml(a)}</li>`).join("");

    $("#interview-report-card").hidden = false;
    interviewState.answered.add(interviewState.currentIndex);
    renderStepper();
    $("#btn-next-question").scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    alert(`Analysis error: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Analyze Answer Performance ↗";
  }
});

$("#btn-next-question")?.addEventListener("click", () => {
  if (interviewState.currentIndex < interviewState.questions.length - 1) {
    renderActiveQuestion(interviewState.currentIndex + 1);
    $("#interview-active-card").scrollIntoView({ behavior: "smooth" });
  } else {
    alert("Great job! You have completed all 7 technical interview rounds.");
    refreshHistory();
  }
});

// Practice history & progress
async function refreshHistory() {
  const summaryWrap = $("#interview-trend-summary");
  const listWrap = $("#interview-session-list");
  try {
    const [trendRes, sessionsRes] = await Promise.all([
      fetch("/api/v1/interview/trend"),
      fetch("/api/v1/interview/sessions"),
    ]);
    const trend = await trendRes.json();
    const sessions = await sessionsRes.json();

    summaryWrap.innerHTML = `
      <div class="history-stat"><strong>${trend.session_count ?? 0}</strong><span>sessions</span></div>
      <div class="history-stat"><strong>${trend.answer_count ?? 0}</strong><span>answers analyzed</span></div>
      <div class="history-stat"><strong>${trend.average_score ?? 0}</strong><span>avg score</span></div>
      <div class="history-stat"><strong>${(trend.improvement ?? 0) >= 0 ? "+" : ""}${trend.improvement ?? 0}</strong><span>vs first session</span></div>
    `;

    if (!sessions.length) {
      listWrap.innerHTML = "<p class='history-empty'>No practice sessions yet — generate questions to start.</p>";
      return;
    }
    listWrap.innerHTML = sessions.map((session) => {
      const pct = session.question_count
        ? Math.round((session.answer_count / session.question_count) * 100)
        : 0;
      const date = new Date(session.created_at).toLocaleString(undefined, {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
      });
      return `
      <div class="history-row" data-session-id="${escapeHtml(session.id)}">
        <div class="history-row-main">
          <div class="history-row-top">
            <strong>${escapeHtml(date)}</strong>
            <span class="tag">${escapeHtml(session.provider)}</span>
          </div>
          <div class="history-progress"><span style="width: ${pct}%"></span></div>
          <span class="history-progress-label">${session.answer_count}/${session.question_count} answered</span>
        </div>
        <div class="history-row-score">${session.avg_score}<span>/100</span></div>
        <button type="button" class="history-delete" data-delete-session="${escapeHtml(session.id)}" title="Delete session">✕</button>
      </div>
    `;
    }).join("");
    listWrap.querySelectorAll("[data-session-id]").forEach((item) => {
      item.addEventListener("click", () => loadSessionDetail(item.dataset.sessionId));
    });
    listWrap.querySelectorAll("[data-delete-session]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        deleteSession(button.dataset.deleteSession);
      });
    });
  } catch (err) {
    summaryWrap.innerHTML = "";
    listWrap.innerHTML = `<p class='detail-description'>History unavailable: ${escapeHtml(err.message)}</p>`;
  }
}

async function loadSessionDetail(sessionId) {
  const detailWrap = $("#interview-session-detail");
  try {
    const res = await fetch(`/api/v1/interview/sessions/${sessionId}`);
    if (!res.ok) throw new Error("Could not load session");
    const session = await res.json();
    const answersByIndex = new Map(
      (session.answers || []).map((answer) => [answer.question_index, answer]),
    );
    detailWrap.hidden = false;
    detailWrap.innerHTML = `
      <h4>Session detail</h4>
      <p class="detail-description">${escapeHtml((session.job_description || "").slice(0, 240))}…</p>
      ${(session.questions || []).map((question, index) => {
        const answer = answersByIndex.get(index);
        return `
          <div class="interview-detail-answer">
            <strong>Q${index + 1} (${escapeHtml(question.category_label || "")}):</strong>
            ${escapeHtml(question.question)}<br/>
            ${answer
              ? `Score <strong>${answer.score}/100</strong> · ${escapeHtml(answer.summary || "")}`
              : "Not practiced yet"}
          </div>
        `;
      }).join("")}
    `;
    detailWrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) {
    alert(`Session detail error: ${err.message}`);
  }
}

async function deleteSession(sessionId) {
  try {
    const res = await fetch(`/api/v1/interview/sessions/${sessionId}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Could not delete session");
    if (interviewState.sessionId === sessionId) interviewState.sessionId = null;
    refreshHistory();
  } catch (err) {
    alert(`Delete error: ${err.message}`);
  }
}

$("#btn-refresh-history")?.addEventListener("click", refreshHistory);
$("#clear-interview")?.addEventListener("click", () => {
  const profileRadio = $("input[name='int-resume-source'][value='profile']");
  if (profileRadio) profileRadio.checked = true;
  $("#int-pdf-source").hidden = true;
  const fileInput = $("#int-resume-pdf");
  if (fileInput) fileInput.value = "";
  $("#int-file-label").textContent = "Click or drag & drop resume PDF";
  $("#int-resume-text").value = "";
  loadInterviewProfile();
});
loadInterviewProfile();
refreshHistory();

$("#clear-interview")?.addEventListener("click", () => {
  $("#interview-jd-text").value = "";
  $("#interview-answer-text").value = "";
  $("#interview-empty").hidden = false;
  $("#interview-active-card").hidden = true;
  $("#interview-report-card").hidden = true;
  if (interviewState.stream) {
    interviewState.stream.getTracks().forEach((t) => t.stop());
    interviewState.stream = null;
  }
});
