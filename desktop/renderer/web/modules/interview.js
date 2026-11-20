import { $, clearFieldError, confirmDecision, escapeHtml, fetchWithTimeout, responseErrorMessage, showErrorToast, showFieldError, announceStatus } from "./helpers.js?v=20260901-error-dialog-minimal-v1";
import { getAiConfigOrShowError } from "./settings.js?v=20260902-shared-components-v1";
import { setupProfileOrPdfResumeSource } from "./resume-source.js?v=20260902-shared-resume-source-v1";
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
  completed: false,
  generating: false,
  advancing: false,
};

function resetInterviewSessionState() {
  interviewState.questions = [];
  interviewState.currentIndex = 0;
  interviewState.sessionId = null;
  interviewState.answered = new Set();
  interviewState.responses = new Map();
  interviewState.analyzing = new Set();
  interviewState.completed = false;
  interviewState.generating = false;
  interviewState.advancing = false;
  syncPracticeCompletion();
  $("#interview-answer-text").value = "";
}

function syncPracticeCompletion() {
  const button = $("#btn-next-question");
  const last = interviewState.currentIndex === interviewState.questions.length - 1;
  const completed = last && interviewState.completed;
  const label = button.querySelector(".lg-button__label");
  label.setAttribute("aria-live", "polite");
  label.textContent = completed ? "Completed" : last ? "Finish" : "Next question";
  if (completed) button.dataset.feedback = "success";
  else delete button.dataset.feedback;
  button.disabled = completed || interviewState.generating || interviewState.advancing || interviewState.analyzing.size > 0;
  $("#interview-completion-status").textContent = interviewState.completed
    ? "Practice complete. Review your feedback or start a new interview."
    : "";
}

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
  syncPracticeCompletion();
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
  startButton.querySelector(".lg-button__label").textContent = response.recordedBlob ? "Re-record" : "Start recording";
  stopButton.hidden = !recordingCurrentQuestion;
  timer.hidden = !recordingCurrentQuestion;
  analyzeButton.disabled = isAnalyzing;
  analyzeButton.querySelector(".lg-button__label").textContent = isAnalyzing
    ? "Analyzing…"
    : recordingCurrentQuestion ? "Stop & analyze" : "Analyze answer";

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
  clearFieldError("#interview-answer-text");
  const q = interviewState.questions[index];
  $("#interview-q-category").textContent = `Question ${index + 1} of 7 · ${stepLabel(q, index)}`;
  $("#interview-q-text").textContent = q.question;
  $("#interview-answer-text").value = responseFor(index).text;
  renderStepper();
  renderAnalysisReport(responseFor(index).analysis);
  syncRecordingControls();
}

function renderStepper() {
  syncPracticeCompletion();
  const stepper = $("#interview-stepper");
  if (!stepper) return;
  stepper.innerHTML = interviewState.questions
    .map((question, idx) => {
      const done = interviewState.answered.has(idx);
      const state = idx === interviewState.currentIndex ? " active" : done ? " done" : "";
      const num = done ? "✓" : idx + 1;
      const label = `Question ${idx + 1}, ${stepLabel(question, idx)}${done ? ", answered" : ""}`;
      return `<button class="step-badge control-glass${state}" data-q="${idx}" type="button"
        aria-label="${escapeHtml(label)}" aria-current="${idx === interviewState.currentIndex ? "step" : "false"}"
        title="${escapeHtml(question.question)}">${num}</button>`;
    })
    .join("");
  stepper.querySelectorAll(".step-badge").forEach((badge) => {
    badge.addEventListener("click", async () => {
      await stopActiveRecording();
      renderActiveQuestion(Number(badge.dataset.q));
      $("#interview-q-text").focus();
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

function currentResumeText() {
  return $("#int-resume-text").value.trim();
}

// The tab module is lazy-loaded. Synchronize with the current radio value in
// case Upload Resume was selected while the import was still in flight.
const interviewResumeSource = setupProfileOrPdfResumeSource({
  sourceInputSelector: "input[name='int-resume-source']",
  sourceControlSelector: '[data-candidate-source="int"]',
  pdfSourceSelector: "#int-pdf-source",
  fileInputSelector: "#int-resume-pdf",
  dropzoneSelector: "#int-dropzone",
  fileLabelSelector: "#int-file-label",
  resumeTextSelector: "#int-resume-text",
});

$("#btn-generate-questions")?.addEventListener("click", async () => {
  const jdText = $("#interview-jd-text").value.trim();
  if (!jdText || Array.from(jdText).length > 20000) {
    showFieldError("#interview-jd-text", jdText ? "Shorten the job description to 20,000 characters or less." : "Paste the target job description.");
    return;
  }
  clearFieldError("#interview-jd-text");
  if (!interviewResumeSource.readyForGeneration()) return;
  const resumeText = currentResumeText();
  if (!resumeText) {
    interviewResumeSource.showSourceError("Add resume information to Profile or choose Resume PDF.", "Upload a text-based resume PDF and wait for extraction.");
    return;
  }
  if (Array.from(resumeText).length > 30000) {
    interviewResumeSource.showSourceError("Shorten your Profile resume to 30,000 characters or choose a shorter PDF.", "Choose a resume PDF with 30,000 characters or less.");
    return;
  }

  const config = getAiConfigOrShowError();
  if (!config) return;

  await stopActiveRecording();
  $("#interview-empty").hidden = true;
  $("#interview-loading").hidden = false;
  $("#interview-active-card").hidden = true;
  $("#interview-report-card").hidden = true;
  resetInterviewSessionState();
  interviewState.generating = true;
  syncPracticeCompletion();

  try {
    const res = await fetchWithTimeout(
      "/api/v1/interview/sessions/stream",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          job_description: jdText,
          resume_text: resumeText,
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
            $("#interview-setup").open = false;
            renderActiveQuestion(0);
            $("#interview-q-text").focus();
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
          const firstQuestion = $("#interview-active-card").hidden;
          $("#interview-loading").hidden = true;
          $("#interview-active-card").hidden = false;
          renderActiveQuestion(Math.min(interviewState.currentIndex, questions.length - 1));
          if (firstQuestion) {
            $("#interview-setup").open = false;
            $("#interview-q-text").focus();
          }
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
    showErrorToast(err, { title: "Question generation failed" });
  } finally {
    interviewState.generating = false;
    syncPracticeCompletion();
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
    interviewState.completed = false;
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
      const recordedBlob = recordedChunks.length ? new Blob(recordedChunks, {
        type: mimeType || "audio/webm",
      }) : null;
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
      if (!recordedBlob && !response.text.trim() && interviewState.currentIndex === questionIndex && !$("#tab-interview").hidden) {
        showFieldError("#interview-answer-text", "No audio was captured. Record again or type your answer.", { focus: false });
      }
      resolveStop(recordedBlob);
    };
    recorder.start();
    clearFieldError("#interview-answer-text");

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
    showErrorToast(`${err.message || "Microphone access failed."} You can type your answer or allow microphone access and try again.`, { title: "Microphone unavailable" });
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
    $("#interview-setup").open = true;
    showFieldError("#interview-jd-text", "Paste the job description for this practice session.");
    return;
  }

  const btn = $("#btn-analyze-answer");
  if (isRecording() && interviewState.recordingQuestionIndex === questionIndex) {
    btn.disabled = true;
    btn.querySelector(".lg-button__label").textContent = "Finishing recording…";
    await stopActiveRecording();
  }

  const answerText = response.text.trim();
  if (!answerText && !response.recordedBlob) {
    showFieldError("#interview-answer-text", "Type your answer or record a response.");
    syncRecordingControls();
    return;
  }

  const config = getAiConfigOrShowError();
  if (!config) return;

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
  interviewState.completed = false;
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
      $("#btn-next-question").scrollIntoView({ block: "nearest" });
    }
  } catch (err) {
    showErrorToast(err, { title: "Answer analysis failed" });
  } finally {
    interviewState.analyzing.delete(questionIndex);
    syncRecordingControls();
  }
});

$("#interview-answer-text")?.addEventListener("input", (event) => {
  if (!interviewState.questions[interviewState.currentIndex]) return;
  responseFor(interviewState.currentIndex).text = event.target.value;
  interviewState.completed = false;
  syncPracticeCompletion();
});

$("#btn-next-question")?.addEventListener("click", async () => {
  if ($("#btn-next-question").disabled || !interviewState.questions.length) return;
  interviewState.advancing = true;
  syncPracticeCompletion();
  try {
    saveVisibleAnswer();
    await stopActiveRecording();
    if (interviewState.currentIndex < interviewState.questions.length - 1) {
      renderActiveQuestion(interviewState.currentIndex + 1);
      $("#interview-q-text").focus();
    } else {
      interviewState.completed = true;
      refreshHistory();
    }
  } finally {
    interviewState.advancing = false;
    syncPracticeCompletion();
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
    if (!trendRes.ok || !sessionsRes.ok) throw new Error("Could not load practice history. Try refreshing the list.");
    const trend = await trendRes.json();
    const sessions = await sessionsRes.json();

    summaryWrap.innerHTML = `
      <div class="history-stat"><strong>${trend.session_count ?? 0}</strong><span>sessions</span></div>
      <div class="history-stat"><strong>${trend.answer_count ?? 0}</strong><span>answers analyzed</span></div>
      <div class="history-stat"><strong>${trend.answer_count ? trend.average_score : "—"}</strong><span>avg score</span></div>
      <div class="history-stat"><strong>${trend.answered_sessions > 1 ? `${trend.improvement >= 0 ? "+" : ""}${trend.improvement}` : "—"}</strong><span>vs first session</span></div>
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
      <div class="history-row">
        <button class="history-open" type="button" data-session-id="${escapeHtml(session.id)}"
          aria-controls="interview-session-detail" aria-expanded="${activeHistoryId === session.id}">
        <span class="history-row-main">
          <span class="history-row-top">
            <strong>${escapeHtml(date)}</strong>
            <span class="tag">${escapeHtml(session.provider)}</span>
          </span>
          <span class="history-progress"><span style="width: ${pct}%"></span></span>
          <span class="history-progress-label">${session.answer_count}/${session.question_count} answered</span>
        </span>
        <span class="history-row-score">${session.answer_count ? session.avg_score : "—"}<span>/100</span></span>
        </button>
        <button type="button" class="history-delete control-glass" data-delete-session="${escapeHtml(session.id)}" aria-label="Delete session from ${escapeHtml(date)}" title="Delete session">×</button>
      </div>
    `;
    }).join("");
    listWrap.querySelectorAll("[data-session-id]").forEach((item) => {
      item.addEventListener("click", () => loadSessionDetail(item.dataset.sessionId));
    });
    listWrap.querySelectorAll("[data-delete-session]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        deleteSession(button);
      });
    });
  } catch (error) {
    showErrorToast(error, { title: "Practice history unavailable" });
  }
}

let activeHistoryId = null;
async function loadSessionDetail(sessionId) {
  const detailWrap = $("#interview-session-detail");
  activeHistoryId = sessionId;
  try {
    const res = await fetch(`/api/v1/interview/sessions/${sessionId}`);
    if (!res.ok) throw new Error("Could not load session");
    const session = await res.json();
    if (activeHistoryId !== sessionId) return;
    const answersByIndex = new Map(
      (session.answers || []).map((answer) => [answer.question_index, answer]),
    );
    detailWrap.hidden = false;
    document.querySelectorAll(".history-open").forEach((button) => {
      button.setAttribute("aria-expanded", String(button.dataset.sessionId === sessionId));
    });
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
    detailWrap.focus();
  } catch (err) {
    if (activeHistoryId !== sessionId) return;
    showErrorToast(err, { title: "Session details unavailable" });
  }
}

async function deleteSession(button) {
  const sessionId = button.dataset.deleteSession;
  const date = button.closest(".history-row").querySelector(".history-row-top strong").textContent;
  const confirmed = await confirmDecision({
    title: "Delete practice session?",
    message: `The questions, answers, and feedback from ${date} will be permanently deleted.`,
    confirmLabel: "Delete session",
    destructive: true,
  });
  if (!confirmed || button.disabled || !button.isConnected) return;
  button.disabled = true;
  try {
    const res = await fetch(`/api/v1/interview/sessions/${sessionId}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Could not delete session");
    if (interviewState.sessionId === sessionId) interviewState.sessionId = null;
    if (activeHistoryId === sessionId) {
      activeHistoryId = null;
      $("#interview-session-detail").hidden = true;
    }
    announceStatus("Practice session deleted.");
    await refreshHistory();
    if (!$("#tab-interview").hidden && document.activeElement === document.body) $("#btn-refresh-history").focus();
  } catch (err) {
    showErrorToast(err, { title: "Could not delete session" });
  } finally {
    button.disabled = false;
  }
}

$("#interview-jump-history")?.addEventListener("click", () => {
  $("#interview-history-heading").focus();
  $("#interview-history-heading").scrollIntoView({ block: "start" });
});
$("#btn-refresh-history")?.addEventListener("click", refreshHistory);
$("#clear-interview")?.addEventListener("click", async () => {
  await stopActiveRecording();
  interviewResumeSource.resetToProfile();
  clearFieldError("#interview-jd-text");
  clearFieldError("#interview-answer-text");
  $("#interview-jd-text").value = "";
  $("#interview-empty").hidden = false;
  $("#interview-setup").open = true;
  $("#interview-active-card").hidden = true;
  $("#interview-report-card").hidden = true;
  resetInterviewSessionState();
  clearRecordingTimer();
  if (interviewState.stream) {
    interviewState.stream.getTracks().forEach((t) => t.stop());
    interviewState.stream = null;
  }
});
refreshHistory();
