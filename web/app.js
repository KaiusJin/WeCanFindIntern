const state = { cursor: null, hasMore: false, loading: false, facets: null, totalCount: 0 };

const $ = (selector) => document.querySelector(selector);

function escapeHtml(text) {
  return (text || "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

function label(value) {
  if (!value) return "Unspecified";
  return value.split("_").map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
}

function workModeLabel(value) {
  const map = { remote: "Remote", hybrid: "Hybrid", in_person: "In-person", onsite: "In-person", unknown: "Work mode not specified" };
  return map[value] || label(value);
}

function skillLabel(value) {
  if (!value) return "";
  const map = { cplusplus: "C++", csharp: "C#", dotnet: ".NET", nodejs: "Node.js", react: "React", vue: "Vue", javascript: "JavaScript", typescript: "TypeScript" };
  return map[value.toLowerCase()] || value.charAt(0).toUpperCase() + value.slice(1);
}

function setOptions(elementId, items, placeholder) {
  const select = $(elementId);
  const current = select.value;
  select.innerHTML = `<option value="">${escapeHtml(placeholder)}</option>` +
    (items || []).map((item) => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.value)} (${item.count})</option>`).join("");
  if (items?.some((item) => item.value === current)) select.value = current;
}

function updateLocationOptions() {
  const facets = state.facets || {};
  setOptions("#country", facets.countries, "All countries");
  setOptions("#region", facets.regions, "All regions");
  setOptions("#city", facets.cities, "All cities");
}

function readFilters() {
  const params = new URLSearchParams();
  const query = $("#query").value.trim();
  const location = $("#location").value.trim();
  if (query) params.set("query", query);
  if (location) params.set("city", location);
  const mappings = {
    "recruiting-term": "recruiting_term",
    country: "country", region: "region", city: "city", "work-mode": "work_mode",
    "opportunity-type": "opportunity_type", "schedule-type": "schedule_type",
    category: "category", skill: "skill",
  };
  for (const [elementId, param] of Object.entries(mappings)) {
    const value = $(`#${elementId}`).value;
    if (value) params.set(param, value);
  }
  if ($("#has-salary").checked) params.set("has_salary", "true");
  const hourlyMin = $("#hourly-min").value.trim();
  if (hourlyMin && Number(hourlyMin) > 0) params.set("hourly_salary_min", hourlyMin);
  const hourlyMax = $("#hourly-max").value.trim();
  if (hourlyMax && Number(hourlyMax) > 0) params.set("hourly_salary_max", hourlyMax);
  params.set("limit", "20");
  return params;
}

function formatDate(value) {
  if (!value) return "Date not specified";
  return new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "short", day: "numeric" }).format(new Date(value));
}

function formatRelativeTime(dateValue) {
  if (!dateValue) return "recently";
  const date = new Date(dateValue);
  const now = new Date();
  const diffSec = Math.max(0, Math.floor((now.getTime() - date.getTime()) / 1000));
  if (diffSec < 60) return "just now";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour}h ago`;
  const diffDay = Math.floor(diffHour / 24);
  if (diffDay < 7) return `${diffDay}d ago`;
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "numeric" }).format(date);
}

function updateLastUpdatedBadge(lastUpdatedIso) {
  if (!lastUpdatedIso) return;
  state.lastUpdated = lastUpdatedIso;
  const relative = formatRelativeTime(lastUpdatedIso);
  const full = new Date(lastUpdatedIso).toLocaleString();
  const textEl = $("#last-updated-text");
  if (textEl) {
    textEl.textContent = `Updated ${relative}`;
    textEl.title = `Last automated job sync: ${full}`;
  }
}

function formatSalary(salary) {
  if (!salary || (salary.minimum == null && salary.maximum == null && salary.annualized_minimum == null && salary.annualized_maximum == null)) return "Salary not disclosed";
  const rawValues = [salary.minimum, salary.maximum].filter((value) => value != null).map(Number);
  if (rawValues.some((value) => !Number.isFinite(value) || value < 0)) return "Salary not disclosed";
  const intervalLimits = {
    hourly: [5, 500], daily: [40, 5000], weekly: [100, 25000],
    monthly: [500, 100000], yearly: [5000, 2000000],
  };
  const limits = intervalLimits[salary.interval];
  if (limits && rawValues.some((value) => value < limits[0] || value > limits[1])) return "Salary not disclosed";
  const hourlyMinimum = salary.interval === "hourly"
    ? salary.minimum
    : (salary.annualized_minimum == null ? null : Number(salary.annualized_minimum) / 2080);
  const hourlyMaximum = salary.interval === "hourly"
    ? salary.maximum
    : (salary.annualized_maximum == null ? null : Number(salary.annualized_maximum) / 2080);
  if (hourlyMinimum == null && hourlyMaximum == null) return "Salary not disclosed";
  const currency = salary.currency || "";
  const symbol = ({ CAD: "$", USD: "$", GBP: "£", EUR: "€" })[currency] || "$";
  const min = hourlyMinimum == null ? "" : `${symbol}${Number(hourlyMinimum).toFixed(2)}`;
  const max = hourlyMaximum == null ? "" : `${symbol}${Number(hourlyMaximum).toFixed(2)}`;
  const range = min && max ? `${min}–${max}` : (min ? `from ${min}` : `up to ${max}`);
  return `${currency} ${range}/hour`.trim();
}

function renderJob(job) {
  const tags = [...new Set([...(job.skill_tags || []).slice(0, 4), job.job_category].filter(Boolean))];
  const recruitingTerm = job.recruiting_term?.display_name;
  return `<article class="job-card" data-id="${job.id}" tabindex="0">
    <div class="job-card-main">
      <div class="company-mark">${escapeHtml((job.company_name || "?").slice(0, 1).toUpperCase())}</div>
      <div class="job-copy">
        <h3>${escapeHtml(job.title)}</h3>
        <p class="company-name">${escapeHtml(job.company_name || "Company not specified")}</p>
        <p class="job-location">${escapeHtml(job.location?.display_name || "Location not specified")} <span>·</span> ${escapeHtml(workModeLabel(job.work_mode))}</p>
      </div>
      <div class="job-date">${formatDate(job.date_posted || job.published_at)}</div>
    </div>
    <div class="job-card-footer">
      <div class="job-tags"><span class="tag accent">${escapeHtml(label(job.opportunity_type))}</span>${recruitingTerm ? `<span class="tag term-tag">${escapeHtml(recruitingTerm)}</span>` : ""}${tags.map((tag) => `<span class="tag">${escapeHtml(job.skill_tags?.includes(tag) ? skillLabel(tag) : label(tag))}</span>`).join("")}</div>
      <span class="salary">${escapeHtml(formatSalary(job.salary))}</span>
    </div>
  </article>`;
}

async function loadJobs({ append = false } = {}) {
  const list = $("#job-list");
  const error = $("#error");
  const loadingIndicator = $("#loading-indicator");
  const endOfResults = $("#end-of-results");

  if (state.loading || (append && (!state.hasMore || !state.cursor))) return;
  state.loading = true;

  if (!append) {
    state.cursor = null;
    list.innerHTML = "";
    $("#empty-state").hidden = true;
    if (loadingIndicator) loadingIndicator.hidden = true;
    if (endOfResults) endOfResults.hidden = true;
    $("#result-status").textContent = "Searching…";
  } else {
    if (loadingIndicator) loadingIndicator.hidden = false;
    if (endOfResults) endOfResults.hidden = true;
  }

  const params = readFilters();
  if (append && state.cursor) params.set("cursor", state.cursor);

  try {
    const response = await fetch(`/api/v1/jobs?${params}`);
    if (!response.ok) throw new Error(`Search failed (${response.status})`);
    const page = await response.json();
    const existingIds = new Set(
      [...list.querySelectorAll(".job-card")].map((card) => card.dataset.id),
    );
    const newItems = page.items.filter((job) => !existingIds.has(job.id));
    list.insertAdjacentHTML("beforeend", newItems.map(renderJob).join(""));
    state.cursor = page.next_cursor;
    state.hasMore = page.has_more;
    if (page.last_updated_at) {
      updateLastUpdatedBadge(page.last_updated_at);
    }
    if (!append) {
      state.totalCount = typeof page.total_count === "number" ? page.total_count : newItems.length;
    }
    const total = state.totalCount ?? 0;
    const formattedCount = total.toLocaleString("en-US");
    $("#result-status").textContent = total > 0 ? `${formattedCount} result${total === 1 ? "" : "s"}` : "0 results";
    $("#empty-state").hidden = Boolean(list.children.length);
    if (endOfResults) {
      endOfResults.hidden = Boolean(page.has_more || !list.children.length);
    }
    error.hidden = true;
  } catch (requestError) {
    error.textContent = requestError.message;
    error.hidden = false;
    $("#result-status").textContent = "Load failed";
  } finally {
    state.loading = false;
    if (loadingIndicator) loadingIndicator.hidden = true;
  }
}

let debounceTimer = null;
function debouncedLoadJobs(waitMs = 300) {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => loadJobs(), waitMs);
}

async function loadFacets() {
  try {
    const response = await fetch("/api/v1/jobs/facets");
    if (!response.ok) throw new Error("facets unavailable");
    state.facets = await response.json();
    if (state.facets.last_updated_at) {
      updateLastUpdatedBadge(state.facets.last_updated_at);
    }
    setOptions("#recruiting-term", state.facets.recruiting_terms, "All recruiting seasons");
    setOptions("#opportunity-type", state.facets.opportunity_types, "All opportunity types");
    setOptions("#schedule-type", state.facets.schedule_types, "All schedules");
    setOptions("#category", state.facets.job_categories, "All categories");
    setOptions("#skill", state.facets.skills, "All skills");
    updateLocationOptions();
  } catch (_) {
    // The results page remains usable if facets are temporarily unavailable.
  }
}

async function openJob(jobId) {
  const dialog = $("#job-dialog");
  const detail = $("#job-detail");
  detail.innerHTML = `<p class="loading-detail">Loading job details…</p>`;
  dialog.showModal();
  syncDialogScrollLock();
  try {
    const response = await fetch(`/api/v1/jobs/${jobId}`);
    if (!response.ok) throw new Error("Could not load job details");
    const job = await response.json();
    const fullJd = `${job.title || "Role"} at ${job.company_name || "Company"}\n\nLocation: ${job.location?.display_name || "Unspecified"}\nWork Mode: ${workModeLabel(job.work_mode)}\nRecruiting Term: ${job.recruiting_term?.display_name || "Unspecified"}\n\nDescription:\n${job.description || ""}`;
    
    // Store current job context for quick AI actions
    state.activeJobContext = {
      id: job.id,
      title: job.title,
      company: job.company_name,
      jd: fullJd,
    };

    detail.innerHTML = `<p class="eyebrow">${escapeHtml(label(job.opportunity_type))} · ${escapeHtml(workModeLabel(job.work_mode))}</p>
      <h2>${escapeHtml(job.title)}</h2><p class="detail-company">${escapeHtml(job.company_name || "Company not specified")}</p>
      <p class="detail-location">${escapeHtml(job.location?.display_name || "Location not specified")} · ${formatDate(job.date_posted)}</p>
      <div class="detail-grid"><div><span>Salary</span><strong>${escapeHtml(formatSalary(job.salary))}</strong></div><div><span>Recruiting term</span><strong>${escapeHtml(job.recruiting_term?.display_name || "Term not specified")}</strong></div><div><span>Skills</span><strong>${escapeHtml(((job.skills?.length ? job.skills : job.skill_tags) || []).slice(0, 8).map(skillLabel).join(", ") || "Skills not specified")}</strong></div></div>
      <div class="detail-description">${job.description ? escapeHtml(job.description).replace(/\n/g, "<br />") : "No detailed description is available for this job."}</div>
      <div class="job-ai-actions">
        <button class="btn-ai-action" type="button" data-ai-target="tab-ats">ATS Match Review ↗</button>
        <button class="btn-ai-action" type="button" data-ai-target="tab-interview">Practice Mock Interview ↗</button>
        <button class="btn-ai-action" type="button" data-ai-target="tab-cover-letter">Generate Cover Letter ↗</button>
      </div>
      <div class="detail-actions" style="margin-top: 16px;">${job.sources?.map((source) => `<a class="primary-button" href="${escapeHtml(source.direct_url || source.url)}" target="_blank" rel="noreferrer">View original job ↗</a>`).join("") || ""}</div>`;
  } catch (requestError) {
    detail.innerHTML = `<div class="notice error">${escapeHtml(requestError.message)}</div>`;
  }
}

function updateSliderFill() {
  const minSlider = $("#hourly-slider-min");
  const maxSlider = $("#hourly-slider-max");
  const fill = $("#slider-track-fill");
  if (!minSlider || !maxSlider || !fill) return;
  const minVal = Number(minSlider.value);
  const maxVal = Number(maxSlider.value);
  const minPct = (minVal / 100) * 100;
  const maxPct = (maxVal / 100) * 100;
  fill.style.left = `${minPct}%`;
  fill.style.width = `${Math.max(0, maxPct - minPct)}%`;
}

function setupInfiniteScroll() {
  const sentinel = $("#infinite-scroll-sentinel");
  if (!sentinel) return;

  const observer = new IntersectionObserver(
    (entries) => {
      const [entry] = entries;
      if (entry.isIntersecting && !state.loading && state.hasMore && state.cursor) {
        loadJobs({ append: true });
      }
    },
    {
      root: null,
      rootMargin: "400px",
      threshold: 0,
    },
  );

  observer.observe(sentinel);
}

function setupBackToTop() {
  const backToTopBtn = $("#back-to-top");
  if (!backToTopBtn) return;
  window.addEventListener(
    "scroll",
    () => {
      backToTopBtn.hidden = window.scrollY < 300;
    },
    { passive: true },
  );
  backToTopBtn.addEventListener("click", () => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
}

// =========================================================
// TAB NAVIGATION CONTROLLER
// =========================================================

function switchTab(targetTabId) {
  document.querySelectorAll(".nav-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === targetTabId);
  });
  document.querySelectorAll(".tab-pane").forEach((pane) => {
    if (pane.id === targetTabId) {
      pane.hidden = false;
      pane.classList.add("active");
    } else {
      pane.hidden = true;
      pane.classList.remove("active");
    }
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

document.querySelectorAll(".nav-tab").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

// =========================================================
// GLOBAL AI SETTINGS & LOCALSTORAGE
// =========================================================

const SETTINGS_STORAGE_KEY = "wecanfindintern_ai_settings_v2";

const aiSettings = {
  selectedModel: "Gemini:gemini-3.7-flash",
  geminiKey: "",
  openaiKey: "",
};

const EYE_SVG_OPEN = `<svg class="eye-svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>`;
const EYE_SVG_OFF = `<svg class="eye-svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>`;

function syncKeyFieldWithModel() {
  const modelVal = $("#setting-selected-model")?.value || "Gemini:gemini-3.7-flash";
  const [provider] = modelVal.split(":");
  const keyInput = $("#setting-api-key");
  const keyLabel = $("#setting-key-label");
  const keyHint = $("#setting-key-hint");

  if (!keyInput || !keyLabel) return;

  if (provider === "OpenAI") {
    keyLabel.textContent = "OpenAI API Key";
    keyInput.placeholder = "sk-proj-... (leave blank to use server default)";
    keyInput.value = aiSettings.openaiKey || "";
    if (keyHint) keyHint.textContent = "Leave blank to use the backend server's default OPENAI_API_KEY.";
  } else {
    keyLabel.textContent = "Gemini API Key";
    keyInput.placeholder = "AIzaSy... (leave blank to use server default)";
    keyInput.value = aiSettings.geminiKey || "";
    if (keyHint) keyHint.textContent = "Leave blank to use the backend server's default GEMINI_API_KEY.";
  }
}

function loadSettings() {
  try {
    const saved = localStorage.getItem(SETTINGS_STORAGE_KEY);
    if (saved) {
      const parsed = JSON.parse(saved);
      Object.assign(aiSettings, parsed);
    }
  } catch (_) {}

  const modelEl = $("#setting-selected-model");
  if (modelEl) modelEl.value = aiSettings.selectedModel || "Gemini:gemini-3.7-flash";
  syncKeyFieldWithModel();
}

function saveSettings() {
  const modelVal = $("#setting-selected-model")?.value || "Gemini:gemini-3.7-flash";
  const [provider] = modelVal.split(":");
  const currentKey = $("#setting-api-key")?.value.trim() || "";

  aiSettings.selectedModel = modelVal;
  if (provider === "OpenAI") {
    aiSettings.openaiKey = currentKey;
  } else {
    aiSettings.geminiKey = currentKey;
  }

  try {
    localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(aiSettings));
  } catch (_) {}

  const feedback = $("#settings-save-feedback");
  if (feedback) {
    feedback.hidden = false;
    setTimeout(() => { feedback.hidden = true; }, 2500);
  }
  setTimeout(() => { $("#settings-dialog")?.close(); }, 500);
}

function getEffectiveAiConfig() {
  const [provider, modelName] = (aiSettings.selectedModel || "Gemini:gemini-3.7-flash").split(":");
  const isGemini = provider === "Gemini";
  return {
    provider: provider,
    model_name: modelName || (isGemini ? "gemini-3.7-flash" : "gpt-4o-mini"),
    api_key: isGemini ? (aiSettings.geminiKey || null) : (aiSettings.openaiKey || null),
  };
}

function syncDialogScrollLock() {
  const anyOpen = Array.from(document.querySelectorAll("dialog")).some((d) => d.open);
  if (anyOpen) {
    document.body.classList.add("modal-open");
    document.documentElement.classList.add("modal-open");
  } else {
    document.body.classList.remove("modal-open");
    document.documentElement.classList.remove("modal-open");
  }
}

document.querySelectorAll("dialog").forEach((dlg) => {
  dlg.addEventListener("close", syncDialogScrollLock);
  dlg.addEventListener("cancel", syncDialogScrollLock);
});

$("#open-settings")?.addEventListener("click", () => {
  loadSettings();
  const dialog = $("#settings-dialog");
  dialog?.showModal();
  syncDialogScrollLock();
});
$("#close-settings")?.addEventListener("click", () => $("#settings-dialog")?.close());
$("#btn-cancel-settings")?.addEventListener("click", () => $("#settings-dialog")?.close());
$("#btn-save-settings")?.addEventListener("click", saveSettings);
$("#settings-dialog")?.addEventListener("click", (e) => {
  if (e.target === $("#settings-dialog")) $("#settings-dialog")?.close();
});

$("#setting-selected-model")?.addEventListener("change", (e) => {
  syncKeyFieldWithModel();
});

$("#toggle-key-visibility")?.addEventListener("click", () => {
  const input = $("#setting-api-key");
  const btn = $("#toggle-key-visibility");
  if (!input || !btn) return;
  if (input.type === "password") {
    input.type = "text";
    btn.innerHTML = EYE_SVG_OFF;
  } else {
    input.type = "password";
    btn.innerHTML = EYE_SVG_OPEN;
  }
});

loadSettings();

// =========================================================
// SECTION 2: ATS RESUME REVIEW
// =========================================================

const atsFileInput = $("#ats-file-input");
if (atsFileInput) {
  atsFileInput.addEventListener("change", async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    $("#ats-file-label").textContent = `Selected: ${file.name}`;
    const formData = new FormData();
    formData.append("file", file);
    try {
      $("#ats-file-label").textContent = `Extracting text from ${file.name}…`;
      const res = await fetch("/api/v1/career/extract-pdf", { method: "POST", body: formData });
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
  });
}

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

  $("#ats-empty").hidden = true;
  $("#ats-loading").hidden = false;
  $("#ats-result-card").hidden = true;

  const config = getEffectiveAiConfig();
  try {
    const res = await fetch("/api/v1/career/ats-review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        resume_text: resumeText,
        job_description: jdText,
        provider: config.provider,
        model_name: config.model_name,
        api_key: config.api_key,
      }),
    });
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

  $("#interview-empty").hidden = true;
  $("#interview-loading").hidden = false;
  $("#interview-active-card").hidden = true;
  $("#interview-report-card").hidden = true;

  const config = getEffectiveAiConfig();
  try {
    const res = await fetch("/api/v1/career/interview/questions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_description: jdText,
        provider: config.provider,
        model_name: config.model_name,
        api_key: config.api_key,
      }),
    });
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
    const res = await fetch("/api/v1/career/interview/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: q.question }),
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

  const config = getEffectiveAiConfig();
  const formData = new FormData();
  formData.append("job_description", jdText);
  formData.append("question_context", q?.question || "");
  formData.append("answer_text", answerText);
  formData.append("provider", config.provider);
  formData.append("model_name", config.model_name || "");
  formData.append("api_key", config.api_key || "");

  if (interviewState.recordedBlob) {
    formData.append("video_file", interviewState.recordedBlob, "answer.webm");
  }

  const btn = $("#btn-analyze-answer");
  btn.disabled = true;
  btn.textContent = "Analyzing Performance with AI…";

  try {
    const res = await fetch("/api/v1/career/interview/analyze", {
      method: "POST",
      body: formData,
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Analysis failed");

    $("#interview-score-num").textContent = data.score;
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

// =========================================================
// SECTION 4: COVER LETTER GENERATOR & EXPORT
// =========================================================

$("#btn-generate-cl")?.addEventListener("click", async () => {
  const resumeText = $("#cl-resume-text").value.trim();
  const jdText = $("#cl-jd-text").value.trim();
  if (!resumeText || !jdText) {
    alert("Please provide both candidate resume/achievements and target job description.");
    return;
  }

  $("#cl-empty").hidden = true;
  $("#cl-loading").hidden = false;
  $("#cl-result-card").hidden = true;
  $("#cl-export-group").hidden = true;

  const config = getEffectiveAiConfig();
  try {
    const res = await fetch("/api/v1/career/cover-letter/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        resume_text: resumeText,
        job_description: jdText,
        provider: config.provider,
        model_name: config.model_name,
        api_key: config.api_key,
        user_info: {
          full_name: $("#cl-full-name").value.trim(),
          email: $("#cl-email").value.trim(),
          phone: $("#cl-phone").value.trim(),
          linkedin: $("#cl-linkedin").value.trim(),
        },
      }),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Generation failed");

    $("#cl-editor-text").value = data.text;
    $("#cl-loading").hidden = true;
    $("#cl-result-card").hidden = false;
    $("#cl-export-group").hidden = false;
  } catch (err) {
    $("#cl-loading").hidden = true;
    $("#cl-empty").hidden = false;
    alert(`Cover Letter Error: ${err.message}`);
  }
});

async function downloadExport(format) {
  const bodyText = $("#cl-editor-text").value.trim();
  if (!bodyText) return;
  const payload = {
    body: bodyText,
    user_info: {
      full_name: $("#cl-full-name").value.trim(),
      email: $("#cl-email").value.trim(),
      phone: $("#cl-phone").value.trim(),
      linkedin: $("#cl-linkedin").value.trim(),
    },
    format,
  };

  try {
    const res = await fetch("/api/v1/career/cover-letter/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error("Export failed");
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `Cover_Letter.${format}`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch (err) {
    alert(`Export download error: ${err.message}`);
  }
}

$("#btn-export-docx")?.addEventListener("click", () => downloadExport("docx"));
$("#btn-export-pdf")?.addEventListener("click", () => downloadExport("pdf"));
$("#btn-export-tex")?.addEventListener("click", () => downloadExport("tex"));

$("#clear-cover-letter")?.addEventListener("click", () => {
  $("#cl-resume-text").value = "";
  $("#cl-jd-text").value = "";
  $("#cl-editor-text").value = "";
  $("#cl-empty").hidden = false;
  $("#cl-loading").hidden = true;
  $("#cl-result-card").hidden = true;
  $("#cl-export-group").hidden = true;
});

// =========================================================
// CROSS-TAB JOB DETAIL AI ACTION LINKING
// =========================================================

document.addEventListener("click", (event) => {
  const aiBtn = event.target.closest("[data-ai-target]");
  if (aiBtn && state.activeJobContext) {
    const targetTab = aiBtn.dataset.aiTarget;
    const jd = state.activeJobContext.jd;

    if (targetTab === "tab-ats") {
      $("#ats-jd-text").value = jd;
      switchTab("tab-ats");
      $("#ats-resume-text").focus();
    } else if (targetTab === "tab-interview") {
      $("#interview-jd-text").value = jd;
      switchTab("tab-interview");
      $("#btn-generate-questions")?.scrollIntoView({ behavior: "smooth" });
    } else if (targetTab === "tab-cover-letter") {
      $("#cl-jd-text").value = jd;
      switchTab("tab-cover-letter");
      $("#cl-resume-text").focus();
    }
    $("#job-dialog")?.close();
  }
});

// =========================================================
// SEARCH AND FILTER EVENT LISTENERS
// =========================================================

$("#search-form").addEventListener("submit", (event) => { event.preventDefault(); loadJobs(); });
$("#refresh").addEventListener("click", () => loadJobs());
$(".filters-panel").addEventListener("change", (event) => {
  if (event.target.id !== "hourly-min" && event.target.id !== "hourly-max" && !event.target.classList.contains("dual-range-slider")) {
    loadJobs();
  }
});
$("#hourly-slider-min").addEventListener("input", (event) => {
  let minVal = Number(event.target.value);
  const maxVal = Number($("#hourly-slider-max").value);
  if (minVal > maxVal) {
    minVal = maxVal;
    event.target.value = minVal;
  }
  $("#hourly-min").value = minVal > 0 ? minVal : "";
  updateSliderFill();
  debouncedLoadJobs(200);
});
$("#hourly-slider-max").addEventListener("input", (event) => {
  let maxVal = Number(event.target.value);
  const minVal = Number($("#hourly-slider-min").value);
  if (maxVal < minVal) {
    maxVal = minVal;
    event.target.value = maxVal;
  }
  $("#hourly-max").value = maxVal < 100 ? maxVal : "";
  updateSliderFill();
  debouncedLoadJobs(200);
});
$("#hourly-min").addEventListener("input", (event) => {
  const val = Number(event.target.value);
  const maxSliderVal = Number($("#hourly-slider-max").value);
  if (!isNaN(val) && val >= 0) {
    const clamped = Math.min(val, maxSliderVal, 100);
    $("#hourly-slider-min").value = clamped;
  } else {
    $("#hourly-slider-min").value = 0;
  }
  updateSliderFill();
  debouncedLoadJobs(300);
});
$("#hourly-max").addEventListener("input", (event) => {
  const val = Number(event.target.value);
  const minSliderVal = Number($("#hourly-slider-min").value);
  if (!isNaN(val) && val > 0) {
    const clamped = Math.max(minSliderVal, Math.min(val, 100));
    $("#hourly-slider-max").value = clamped;
  } else if (!event.target.value.trim()) {
    $("#hourly-slider-max").value = 100;
  }
  updateSliderFill();
  debouncedLoadJobs(300);
});
$("#clear-filters").addEventListener("click", () => {
  $("#search-form").reset(); $("#location").value = "";
  ["#recruiting-term", "#country", "#region", "#city", "#work-mode", "#opportunity-type", "#schedule-type", "#category", "#skill"].forEach((id) => { $(id).value = ""; });
  $("#has-salary").checked = false;
  $("#hourly-min").value = "";
  $("#hourly-max").value = "";
  $("#hourly-slider-min").value = 0;
  $("#hourly-slider-max").value = 100;
  updateSliderFill();
  loadJobs();
});
document.addEventListener("click", (event) => {
  const card = event.target.closest(".job-card");
  if (card && !event.target.closest(".btn-ai-action")) openJob(card.dataset.id);
  const quick = event.target.closest("[data-query], [data-term]");
  if (quick) {
    if (quick.dataset.query) $("#query").value = quick.dataset.query;
    if (quick.dataset.term) $("#recruiting-term").value = quick.dataset.term;
    loadJobs();
  }
});
$("#close-dialog").addEventListener("click", () => $("#job-dialog").close());
$("#job-dialog").addEventListener("click", (event) => { if (event.target === $("#job-dialog")) $("#job-dialog").close(); });

updateSliderFill();
loadFacets();
loadJobs();
setupInfiniteScroll();
setupBackToTop();

