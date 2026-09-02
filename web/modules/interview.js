import { $, escapeHtml, fetchWithTimeout, responseErrorMessage, setupDropzone, showErrorDialog, showSuccessDialog } from "./helpers.js?v=20260901-error-dialog-minimal-v1";
import { validateAiConfig } from "./settings.js?v=20260901-settings-v1";
import { extractResumePdf, loadProfileContext } from "./resume-source.js";
import { readSseEvents } from "./sse.js";

// =========================================================
// SECTION 3: AI INTERVIEW COACH & AUDIO RECORDER
// =========================================================

const interviewState = {
  questions: [],
  currentIndex: 0,
  sessionId: null,
  answered: new Set(),
  responses: new Map(),
  analyzing: new Set(),
  mediaRecorder: null,
  recordingQuestionIndex: null,
  stopRecordingPromise: null,
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

function responseFor(index) {
  if (!interviewState.responses.has(index)) {
    interviewState.responses.set(index, {
      text: "",
      recordedBlob: null,
      recordingDurationSeconds: 0,
      analysis: null,
    });
  }
  return interviewState.responses.get(index);
}

function saveVisibleAnswer() {
  if (!interviewState.questions[interviewState.currentIndex]) return;
  responseFor(interviewState.currentIndex).text = $("#interview-answer-text").value;
}

function isRecording() {
  return Boolean(
    interviewState.mediaRecorder
    && interviewState.mediaRecorder.state !== "inactive",
  );
}

function renderAnalysisReport(data) {
  const reportCard = $("#interview-report-card");
  if (!data) {
    reportCard.hidden = true;
    return;
  }

  const interviewScore = Number(data.score) || 0;
  $("#interview-score-num").textContent = interviewScore;
  $("#interview-level-pill").textContent = interviewScore >= 80
    ? "Strong Performance"
    : interviewScore >= 60 ? "Solid Performance" : "Needs Practice";
  $("#interview-summary-text").textContent = data.summary || "";

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
  } else {
    transcriptBlock.hidden = true;
  }

  const criteriaBlock = $("#criteria-results-block");
  const criteriaList = $("#interview-criteria-wrap");
  const results = data.criteria_results || [];
  if (results.length) {
    criteriaBlock.hidden = false;
    criteriaList.innerHTML = criteriaResultsMarkup(results);
  } else {
    criteriaBlock.hidden = true;
  }

  const timelineWrap = $("#interview-timeline-wrap");
  timelineWrap.innerHTML = (data.timeline || []).map((item) => {
    const section = item.section || item.type || "Observation";
    return `
      <div class="timeline-event-item">
        <span class="timeline-ts">${escapeHtml(section)}</span>
        <span class="timeline-obs">${escapeHtml(item.observation)}</span>
      </div>
    `;
  }).join("") || "<p class='detail-description'>No specific answer-phase notes.</p>";

  $("#interview-advice-wrap").innerHTML = (data.advice || [])
    .map((advice) => `<li>${escapeHtml(advice)}</li>`)
    .join("");
  reportCard.hidden = false;
}

function syncRecordingControls() {
  const response = responseFor(interviewState.currentIndex);
  const recordingCurrentQuestion = isRecording()
    && interviewState.recordingQuestionIndex === interviewState.currentIndex;
  const isAnalyzing = interviewState.analyzing.has(interviewState.currentIndex);
  const startButton = $("#btn-start-record");
  const stopButton = $("#btn-stop-record");
  const analyzeButton = $("#btn-analyze-answer");
  const timer = $("#recording-timer");
  const status = $("#recording-inline-status");

  startButton.hidden = recordingCurrentQuestion;
  startButton.disabled = isAnalyzing;
  startButton.textContent = response.recordedBlob ? "↺ Re-record" : "🎙 Start recording";
  stopButton.hidden = !recordingCurrentQuestion;
  timer.hidden = !recordingCurrentQuestion;
  analyzeButton.disabled = isAnalyzing;
  analyzeButton.textContent = isAnalyzing
    ? "Analyzing…"
    : recordingCurrentQuestion ? "Stop & analyze ↗" : "Analyze answer ↗";

  if (recordingCurrentQuestion) {
    status.textContent = "Recording — click Analyze when you finish.";
    status.hidden = false;
  } else if (response.recordedBlob) {
    status.textContent = "Recording saved for this question.";
    status.hidden = false;
  } else {
    status.textContent = "";
    status.hidden = true;
  }
}

function renderActiveQuestion(index) {
  if (!interviewState.questions[index]) return;
  saveVisibleAnswer();
  interviewState.currentIndex = index;
  const q = interviewState.questions[index];
  $("#interview-q-category").textContent = q.category_label || `Question ${index + 1}`;
  $("#interview-q-text").textContent = q.question;
  $("#interview-answer-text").value = responseFor(index).text;
  renderStepper();
  renderAnalysisReport(responseFor(index).analysis);
  syncRecordingControls();
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
    badge.addEventListener("click", async () => {
      await stopActiveRecording();
      renderActiveQuestion(Number(badge.dataset.q));
    });
  });
}

function criteriaResultsMarkup(results) {
  const verdictIcon = { met: "✓", partial: "◐", missed: "✕" };
  return (results || []).map((result) => `
    <div class="criterion-result criterion-${escapeHtml(result.verdict || "missed")}">
      <span class="criterion-verdict">${verdictIcon[result.verdict] || "✕"}</span>
      <span class="criterion-text"><strong>${escapeHtml(result.criterion || "")}</strong>${result.note ? ` — ${escapeHtml(result.note)}` : ""}</span>
    </div>
  `).join("");
}

// Candidate source: saved Profile or uploaded resume PDF (same flow as the
// Cover Letter section).
async function loadInterviewProfile() {
  try {
    const context = await loadProfileContext();
    const text = context.resume_text || "";
    $("#int-resume-text").value = text;
  } catch (error) {
    $("#int-resume-text").value = "";
    showErrorDialog(error, { title: "Could not load Profile" });
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
  try {
    const result = await extractResumePdf(file);
    $("#int-resume-text").value = result.text;
    if (label) label.textContent = `✓ Extracted from ${file.name}`;
  } catch (error) {
    if (label) label.textContent = "Click or drag & drop resume PDF";
    $("#int-resume-text").value = "";
    showErrorDialog(error, { title: "Resume upload failed" });
  }
}

function syncInterviewResumeSource({ resetPdf = false } = {}) {
  const isPdf = isPdfResumeSource();
  $("#int-pdf-source").hidden = !isPdf;
  if (isPdf) {
    if (resetPdf) {
      $("#int-resume-text").value = "";
      $("#int-file-label").textContent = "Click or drag & drop resume PDF";
    }
  } else loadInterviewProfile();
}

document.querySelectorAll("input[name='int-resume-source']").forEach((input) => input.addEventListener("change", () => {
  syncInterviewResumeSource({ resetPdf: true });
}));
$("#int-resume-pdf")?.addEventListener("change", (event) => extractInterviewPdf(event.target.files?.[0]));
setupDropzone($("#int-dropzone"), (files) => extractInterviewPdf(files[0]));
// The tab module is lazy-loaded. Synchronize with the current radio value in
// case Upload Resume was selected while the import was still in flight.
syncInterviewResumeSource({ resetPdf: true });

$("#btn-generate-questions")?.addEventListener("click", async () => {
  const jdText = $("#interview-jd-text").value.trim();
  if (!jdText) {
    showErrorDialog("The job description is empty.", { title: "Job description required", guidance: "Paste the target job description, then generate questions again." });
    return;
  }
  if (isPdfResumeSource() && !$("#int-resume-pdf")?.files?.length && !currentResumeText()) {
    showErrorDialog("No uploaded resume content is available.", { title: "Resume required", guidance: "Upload a readable resume PDF and wait for extraction to finish before generating questions." });
    return;
  }
  if (!currentResumeText()) {
    showErrorDialog("Your Profile does not contain resume information.", { title: "Resume information required", guidance: "Add Profile data or switch to Upload Resume, then try again." });
    return;
  }

  let config;
  try {
    config = validateAiConfig();
  } catch (err) {
    showErrorDialog(err, { title: "AI settings required" });
    return;
  }

  await stopActiveRecording();
  $("#interview-empty").hidden = true;
  $("#interview-loading").hidden = false;
  $("#interview-active-card").hidden = true;
  $("#interview-report-card").hidden = true;
  interviewState.questions = [];
  interviewState.currentIndex = 0;
  interviewState.sessionId = null;
  interviewState.answered = new Set();
  interviewState.responses = new Map();
  interviewState.analyzing = new Set();
  $("#interview-answer-text").value = "";

  try {
    const res = await fetchWithTimeout(
      "/api/v1/interview/sessions/stream",
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
      180000,
    );
    if (!res.ok || !res.body) {
      throw new Error(await responseErrorMessage(res, "Interview questions could not be generated."));
    }

    let questions = [];
    for await (const event of readSseEvents(res)) {
        if (event.type === "question") {
          questions.push(event.question);
          interviewState.questions = questions;
          if (questions.length === 1) {
            interviewState.sessionId = null;
            interviewState.answered = new Set();
            interviewState.responses = new Map();
            interviewState.analyzing = new Set();
            $("#interview-loading").hidden = true;
            $("#interview-active-card").hidden = false;
            renderActiveQuestion(0);
          } else {
            renderStepper();
          }
        } else if (event.type === "error") {
          throw new Error(event.detail || "Question generation failed");
        } else if (event.type === "done") {
          questions = Array.isArray(event.questions) ? event.questions : [];
          if (!questions.length) throw new Error("Model returned no questions");
          interviewState.questions = questions;
          interviewState.sessionId = event.session_id || null;
          $("#interview-loading").hidden = true;
          $("#interview-active-card").hidden = false;
          renderActiveQuestion(Math.min(interviewState.currentIndex, questions.length - 1));
        }
    }
    if (!interviewState.questions.length) {
      throw new Error("Model returned no questions");
    }
    if (interviewState.sessionId) refreshHistory();
  } catch (err) {
    if (!interviewState.questions.length) {
      $("#interview-empty").hidden = false;
      $("#interview-active-card").hidden = true;
    }
    showErrorDialog(err, { title: "Question generation failed" });
  } finally {
    $("#interview-loading").hidden = true;
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

function clearRecordingTimer() {
  clearInterval(interviewState.timerInterval);
  interviewState.timerInterval = null;
}

async function stopActiveRecording() {
  const recorder = interviewState.mediaRecorder;
  if (!recorder) return null;
  const pendingStop = interviewState.stopRecordingPromise;
  if (recorder.state !== "inactive") recorder.stop();
  clearRecordingTimer();
  syncRecordingControls();
  return pendingStop || null;
}

$("#btn-start-record")?.addEventListener("click", async () => {
  try {
    if (isRecording()) await stopActiveRecording();
    const stream = await initMicrophone();
    const questionIndex = interviewState.currentIndex;
    const response = responseFor(questionIndex);
    const recordedChunks = [];
    const mimeType = MediaRecorder.isTypeSupported("audio/webm")
      ? "audio/webm"
      : "";
    const recorder = new MediaRecorder(
      stream,
      mimeType ? { mimeType } : undefined,
    );
    interviewState.mediaRecorder = recorder;
    interviewState.recordingQuestionIndex = questionIndex;
    response.recordedBlob = null;
    response.recordingDurationSeconds = 0;

    let resolveStop;
    interviewState.stopRecordingPromise = new Promise((resolve) => {
      resolveStop = resolve;
    });
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) recordedChunks.push(event.data);
    };
    recorder.onstop = () => {
      const recordedBlob = new Blob(recordedChunks, {
        type: mimeType || "audio/webm",
      });
      response.recordedBlob = recordedBlob;
      response.recordingDurationSeconds = interviewState.secondsElapsed;
      stream.getTracks().forEach((t) => t.stop());
      if (interviewState.stream === stream) interviewState.stream = null;
      if (interviewState.mediaRecorder === recorder) {
        interviewState.mediaRecorder = null;
        interviewState.recordingQuestionIndex = null;
        interviewState.stopRecordingPromise = null;
      }
      clearRecordingTimer();
      syncRecordingControls();
      resolveStop(recordedBlob);
    };
    recorder.start();

    interviewState.secondsElapsed = 0;
    $("#recording-time-text").textContent = "00:00";
    interviewState.timerInterval = setInterval(() => {
      interviewState.secondsElapsed += 1;
      const m = String(Math.floor(interviewState.secondsElapsed / 60)).padStart(2, "0");
      const s = String(interviewState.secondsElapsed % 60).padStart(2, "0");
      $("#recording-time-text").textContent = `${m}:${s}`;
    }, 1000);
    syncRecordingControls();
  } catch (err) {
    setRecordStatus("You can type your answer in the text box instead.");
    showErrorDialog(err, { title: "Microphone unavailable", guidance: "Allow microphone access in your browser settings, or type your answer instead." });
  }
});

$("#btn-stop-record")?.addEventListener("click", async () => {
  await stopActiveRecording();
});

$("#btn-analyze-answer")?.addEventListener("click", async () => {
  const jdText = $("#interview-jd-text").value.trim();
  const questionIndex = interviewState.currentIndex;
  const q = interviewState.questions[questionIndex];
  const response = responseFor(questionIndex);
  saveVisibleAnswer();

  if (!jdText) {
    showErrorDialog("The job description for this practice session is missing.", { title: "Job description required" });
    return;
  }

  const btn = $("#btn-analyze-answer");
  if (isRecording() && interviewState.recordingQuestionIndex === questionIndex) {
    btn.disabled = true;
    btn.textContent = "Finishing recording…";
    await stopActiveRecording();
  }

  const answerText = response.text.trim();
  if (!answerText && !response.recordedBlob) {
    showErrorDialog("No answer was provided.", { title: "Answer required", guidance: "Record an audio answer or type your response before requesting analysis." });
    syncRecordingControls();
    return;
  }

  let config;
  try {
    config = validateAiConfig();
  } catch (err) {
    showErrorDialog(err, { title: "AI settings required" });
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

  if (response.recordedBlob) {
    formData.append("audio_file", response.recordedBlob, `answer-${questionIndex + 1}.webm`);
  }
  if (interviewState.sessionId) {
    formData.append("session_id", interviewState.sessionId);
    formData.append("question_index", String(questionIndex));
  }
  const evaluationCriteria = q?.evaluation_criteria || q?.eval_criteria || [];
  if (evaluationCriteria.length) {
    formData.append(
      "evaluation_criteria",
      evaluationCriteria.map((c) => `- ${c}`).join("\n"),
    );
  }

  interviewState.analyzing.add(questionIndex);
  syncRecordingControls();

  try {
    const res = await fetchWithTimeout("/api/v1/interview/analyze", { method: "POST", body: formData }, 180000);
    if (!res.ok) throw new Error(await responseErrorMessage(res, "The interview answer could not be analyzed."));
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Analysis failed");

    response.analysis = data;
    if (!answerText && data.transcript) response.text = data.transcript;
    interviewState.answered.add(questionIndex);
    renderStepper();
    if (interviewState.currentIndex === questionIndex) {
      $("#interview-answer-text").value = response.text;
      renderAnalysisReport(data);
      $("#btn-next-question").scrollIntoView({ behavior: "smooth" });
    }
  } catch (err) {
    showErrorDialog(err, { title: "Answer analysis failed" });
  } finally {
    interviewState.analyzing.delete(questionIndex);
    syncRecordingControls();
  }
});

$("#interview-answer-text")?.addEventListener("input", (event) => {
  if (!interviewState.questions[interviewState.currentIndex]) return;
  responseFor(interviewState.currentIndex).text = event.target.value;
});

$("#btn-next-question")?.addEventListener("click", async () => {
  saveVisibleAnswer();
  await stopActiveRecording();
  if (interviewState.currentIndex < interviewState.questions.length - 1) {
    renderActiveQuestion(interviewState.currentIndex + 1);
    $("#interview-active-card").scrollIntoView({ behavior: "smooth" });
  } else {
    showSuccessDialog("You completed all 7 technical interview rounds.", { title: "Practice session complete" });
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
    listWrap.innerHTML = "";
    showErrorDialog(err, { title: "Practice history unavailable" });
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
              ? `Score <strong>${answer.score}/100</strong> · ${escapeHtml(answer.summary || "")}
                ${answer.criteria_results?.length
                  ? `<div class="interview-criteria-wrap">${criteriaResultsMarkup(answer.criteria_results)}</div>`
                  : ""}`
              : "Not practiced yet"}
          </div>
        `;
      }).join("")}
    `;
    detailWrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) {
    showErrorDialog(err, { title: "Session details unavailable" });
  }
}

async function deleteSession(sessionId) {
  try {
    const res = await fetch(`/api/v1/interview/sessions/${sessionId}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Could not delete session");
    if (interviewState.sessionId === sessionId) interviewState.sessionId = null;
    refreshHistory();
  } catch (err) {
    showErrorDialog(err, { title: "Could not delete session" });
  }
}

$("#btn-refresh-history")?.addEventListener("click", refreshHistory);
$("#clear-interview")?.addEventListener("click", async () => {
  await stopActiveRecording();
  const profileRadio = $("input[name='int-resume-source'][value='profile']");
  if (profileRadio) profileRadio.checked = true;
  $("#int-pdf-source").hidden = true;
  const fileInput = $("#int-resume-pdf");
  if (fileInput) fileInput.value = "";
  $("#int-file-label").textContent = "Click or drag & drop resume PDF";
  $("#int-resume-text").value = "";
  loadInterviewProfile();
  $("#interview-jd-text").value = "";
  $("#interview-answer-text").value = "";
  $("#interview-empty").hidden = false;
  $("#interview-active-card").hidden = true;
  $("#interview-report-card").hidden = true;
  interviewState.questions = [];
  interviewState.currentIndex = 0;
  interviewState.sessionId = null;
  interviewState.answered = new Set();
  interviewState.responses = new Map();
  interviewState.analyzing = new Set();
  clearRecordingTimer();
  if (interviewState.stream) {
    interviewState.stream.getTracks().forEach((t) => t.stop());
    interviewState.stream = null;
  }
});
refreshHistory();
