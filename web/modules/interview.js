import { $, escapeHtml, fetchWithTimeout } from "./helpers.js";
import { validateAiConfig } from "./settings.js";

// =========================================================
// SECTION 3: AI INTERVIEW COACH & AUDIO RECORDER
// =========================================================

const interviewState = {
  questions: [],
  currentIndex: 0,
  sessionId: null,
  mediaRecorder: null,
  recordedChunks: [],
  recordedBlob: null,
  stream: null,
  timerInterval: null,
  secondsElapsed: 0,
};

function renderActiveQuestion(index) {
  if (!interviewState.questions[index]) return;
  interviewState.currentIndex = index;
  const q = interviewState.questions[index];
  $("#interview-q-category").textContent = q.category_label || `Question ${index + 1}`;
  $("#interview-q-text").textContent = q.question;
  document.querySelectorAll(".step-badge").forEach((badge, idx) => {
    badge.classList.toggle("active", idx === index);
  });
  $("#interview-report-card").hidden = true;
  $("#interview-answer-text").value = "";
}

document.querySelectorAll(".step-badge").forEach((badge) => {
  badge.addEventListener("click", () => {
    const idx = Number(badge.dataset.q);
    renderActiveQuestion(idx);
  });
});

$("#btn-generate-questions")?.addEventListener("click", async () => {
  const jdText = $("#interview-jd-text").value.trim();
  if (!jdText) {
    alert("Please enter a job description to generate interview questions.");
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
          provider: config.provider,
          model_name: config.model_name,
          api_key: config.api_key,
          api_base: config.api_base || "",
        }),
      },
      120000,
    );
    const data = await res.json();
    if (!data.ok || !data.questions?.length) throw new Error(data.error || "Failed to generate questions");

    interviewState.questions = data.questions;
    interviewState.sessionId = data.session_id || null;
    renderActiveQuestion(0);

    $("#interview-loading").hidden = true;
    $("#interview-active-card").hidden = false;
  } catch (err) {
    $("#interview-loading").hidden = true;
    $("#interview-empty").hidden = false;
    alert(`Question generation failed: ${err.message}`);
  }
});

// TTS Audio
$("#btn-play-tts")?.addEventListener("click", async () => {
  const q = interviewState.questions[interviewState.currentIndex];
  if (!q) return;
  const btn = $("#btn-play-tts");
  const originalText = btn.innerHTML;
  btn.innerHTML = "<span>⏳ Loading audio…</span>";
  try {
    const res = await fetch("/api/v1/interview/tts", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ text: q.question }),
    });
    if (!res.ok) throw new Error("TTS audio unavailable");
    const blob = await res.blob();
    const audioUrl = URL.createObjectURL(blob);
    const player = $("#interview-audio-player");
    player.src = audioUrl;
    player.play();
    btn.innerHTML = "<span>Playing…</span>";
    player.onended = () => {
      btn.innerHTML = originalText;
    };
  } catch (err) {
    btn.innerHTML = originalText;
    alert(`Audio error: ${err.message}`);
  }
});

// Microphone Recording (audio only; transcription happens locally on the server)
async function initMicrophone() {
  if (interviewState.stream) return interviewState.stream;
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  interviewState.stream = stream;
  return stream;
}

function setAudioStatus(icon, text) {
  $("#audio-status-icon").textContent = icon;
  $("#audio-status-text").textContent = text;
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
      setAudioStatus(
        "🎙️",
        "Recording saved. Click 'Analyze' to transcribe locally and get feedback, or re-record.",
      );
    };
    interviewState.mediaRecorder.start();

    setAudioStatus("🔴", "Recording… speak your answer clearly.");
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
    setAudioStatus(
      "🎤",
      `Microphone access failed (${err.message}). You can still type your answer below.`,
    );
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
  $("#btn-start-record").textContent = "Re-record Answer";
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

    const timelineWrap = $("#interview-timeline-wrap");
    timelineWrap.innerHTML = (data.timeline || []).map((t) => `
      <div class="timeline-event-item">
        <span class="timeline-ts">${escapeHtml(t.timestamp)}</span>
        <span class="timeline-obs"><strong>${escapeHtml(t.type)}:</strong> ${escapeHtml(t.observation)}</span>
      </div>
    `).join("") || "<p class='detail-description'>No specific timeline flags noted.</p>";

    const adviceWrap = $("#interview-advice-wrap");
    adviceWrap.innerHTML = (data.advice || []).map((a) => `<li>${escapeHtml(a)}</li>`).join("");

    $("#interview-report-card").hidden = false;
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
    alert("Great job! You have completed all 3 mock interview rounds.");
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
      <div class="trend-stat"><strong>${trend.session_count ?? 0}</strong>practice sessions</div>
      <div class="trend-stat"><strong>${trend.answer_count ?? 0}</strong>answers analyzed</div>
      <div class="trend-stat"><strong>${trend.average_score ?? 0}</strong>average score</div>
      <div class="trend-stat"><strong>${(trend.improvement ?? 0) >= 0 ? "+" : ""}${trend.improvement ?? 0}</strong>change since first session</div>
    `;

    if (!sessions.length) {
      listWrap.innerHTML = "<p class='detail-description'>No practice sessions yet. Generate questions to start.</p>";
      return;
    }
    listWrap.innerHTML = sessions.map((session) => `
      <div class="interview-session-item" data-session-id="${escapeHtml(session.id)}">
        <span>${new Date(session.created_at).toLocaleString()} · ${escapeHtml(session.provider)}</span>
        <span>${session.answer_count}/${session.question_count} answered ·
          <span class="interview-session-score">${session.avg_score}/100</span></span>
        <button type="button" class="agent-memory-delete" data-delete-session="${escapeHtml(session.id)}">✕</button>
      </div>
    `).join("");
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
