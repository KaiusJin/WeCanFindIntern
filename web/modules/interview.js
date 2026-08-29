import { $, escapeHtml, fetchWithTimeout } from "./helpers.js";
import { validateAiConfig } from "./settings.js";

// =========================================================
// SECTION 3: AI INTERVIEW COACH & VIDEO RECORDER
// =========================================================

const interviewState = {
  questions: [],
  currentIndex: 0,
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
      "/api/v1/interview/questions",
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

// Camera & Recording
async function initWebcam() {
  try {
    if (interviewState.stream) return;
    interviewState.stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
    const video = $("#webcam-preview");
    video.srcObject = interviewState.stream;
    $("#webcam-overlay").hidden = true;
  } catch (err) {
    $("#webcam-overlay").textContent = `Camera access: ${err.message}. You can still type your answer below.`;
  }
}

$("#btn-toggle-cam")?.addEventListener("click", () => {
  if (interviewState.stream) {
    interviewState.stream.getTracks().forEach((t) => t.stop());
    interviewState.stream = null;
    $("#webcam-preview").srcObject = null;
    $("#webcam-overlay").textContent = "Camera paused. Click to restart.";
    $("#webcam-overlay").hidden = false;
  } else {
    initWebcam();
  }
});

$("#btn-start-record")?.addEventListener("click", async () => {
  await initWebcam();
  if (!interviewState.stream) return;
  interviewState.recordedChunks = [];
  try {
    interviewState.mediaRecorder = new MediaRecorder(interviewState.stream);
    interviewState.mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) interviewState.recordedChunks.push(e.data);
    };
    interviewState.mediaRecorder.onstop = () => {
      interviewState.recordedBlob = new Blob(interviewState.recordedChunks, { type: "video/webm" });
    };
    interviewState.mediaRecorder.start();

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
    alert(`Recording could not start: ${err.message}`);
  }
});

$("#btn-stop-record")?.addEventListener("click", () => {
  if (interviewState.mediaRecorder && interviewState.mediaRecorder.state !== "inactive") {
    interviewState.mediaRecorder.stop();
  }
  clearInterval(interviewState.timerInterval);
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
    formData.append("video_file", interviewState.recordedBlob, "answer.webm");
  }

  const btn = $("#btn-analyze-answer");
  btn.disabled = true;
  btn.textContent = "Analyzing Performance with AI…";

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
  }
});

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
