const state = { cursor: null, hasMore: false, loading: false, facets: null, totalCount: 0 };
const trackerState = {
  applications: [],
  stats: {},
  trackedJobIds: new Set(),
  trackedJobs: new Map(),
  selectedIds: new Set(),
  attentionItems: [],
  analytics: null,
  loading: false,
  page: 1,
  pageSize: 50,
  total: 0,
  totalPages: 0,
  view: localStorage.getItem("wecan_tracker_view") || "list",
  requestVersion: 0,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => document.querySelectorAll(selector);

function escapeHtml(text) {
  return (text || "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

function renderMarkdown(rawText) {
  if (!rawText) return "";
  let text = String(rawText).replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim();
  text = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  text = text.replace(/```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g, (_, _lang, code) => `<pre class="md-code-block"><code>${code.trim()}</code></pre>`);
  text = text.replace(/`([^`\n]+)`/g, '<code class="md-inline-code">$1</code>');
  text = text.replace(/^[ \t]*([-*_][ \t]*){3,}$/gm, '<hr class="md-hr" />');
  text = text.replace(/^[ \t]*######[ \t]+([^\n]+)$/gm, '<h6 class="md-h6">$1</h6>');
  text = text.replace(/^[ \t]*#####[ \t]+([^\n]+)$/gm, '<h5 class="md-h5">$1</h5>');
  text = text.replace(/^[ \t]*####[ \t]+([^\n]+)$/gm, '<h4 class="md-h4">$1</h4>');
  text = text.replace(/^[ \t]*###[ \t]+([^\n]+)$/gm, '<h3 class="md-h3">$1</h3>');
  text = text.replace(/^[ \t]*##[ \t]+([^\n]+)$/gm, '<h2 class="md-h2">$1</h2>');
  text = text.replace(/^[ \t]*#[ \t]+([^\n]+)$/gm, '<h1 class="md-h1">$1</h1>');
  text = text.replace(/\*\*\*([^*]+)\*\*\*/g, "<strong><em>$1</em></strong>");
  text = text.replace(/___([^_]+)___/g, "<strong><em>$1</em></strong>");
  text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  text = text.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
  text = text.replace(/_([^_\n]+)_/g, "<em>$1</em>");
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s\)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer" class="md-link">$1 ↗</a>');

  const lines = text.split("\n");
  const output = [];
  let inUl = false;
  let inOl = false;
  let currentParagraph = [];

  const flushParagraph = () => {
    if (currentParagraph.length > 0) {
      output.push(`<p class="md-p">${currentParagraph.join("<br />")}</p>`);
      currentParagraph = [];
    }
  };

  const closeLists = () => {
    if (inUl) { output.push("</ul>"); inUl = false; }
    if (inOl) { output.push("</ol>"); inOl = false; }
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();
    if (!trimmed) {
      flushParagraph();
      closeLists();
      continue;
    }
    if (/^<(h[1-6]|hr|pre)/.test(trimmed)) {
      flushParagraph();
      closeLists();
      output.push(trimmed);
      continue;
    }
    const ulMatch = trimmed.match(/^[\*\-\+]\s+(.*)$/);
    if (ulMatch) {
      flushParagraph();
      if (inOl) { output.push("</ol>"); inOl = false; }
      if (!inUl) { output.push('<ul class="md-ul">'); inUl = true; }
      output.push(`<li class="md-li">${ulMatch[1]}</li>`);
      continue;
    }
    const olMatch = trimmed.match(/^(\d+)\.\s+(.*)$/);
    if (olMatch) {
      flushParagraph();
      if (inUl) { output.push("</ul>"); inUl = false; }
      if (!inOl) { output.push('<ol class="md-ol">'); inOl = true; }
      output.push(`<li class="md-li">${olMatch[2]}</li>`);
      continue;
    }
    closeLists();
    currentParagraph.push(trimmed);
  }
  flushParagraph();
  closeLists();
  return output.join("\n");
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
  const isSaved = trackerState.trackedJobIds?.has(job.id);
  const bookmarkIcon = isSaved
    ? `<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>`
    : `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>`;

  return `<article class="job-card" data-id="${job.id}" tabindex="0">
    <div class="job-card-main">
      <div class="company-mark">${escapeHtml((job.company_name || "?").slice(0, 1).toUpperCase())}</div>
      <div class="job-copy">
        <h3>${escapeHtml(job.title)}</h3>
        <p class="company-name">${escapeHtml(job.company_name || "Company not specified")}</p>
        <p class="job-location">${escapeHtml(job.location?.display_name || "Location not specified")} <span>·</span> ${escapeHtml(workModeLabel(job.work_mode))}</p>
      </div>
      <div style="display:flex; flex-direction:column; align-items:flex-end; gap:6px;">
        <button type="button" class="job-bookmark-btn ${isSaved ? 'saved' : ''}" data-job-id="${job.id}" title="${isSaved ? 'Tracked in Pipeline' : 'Bookmark / Track Job'}">
          ${bookmarkIcon}
        </button>
        <div class="job-date">${formatDate(job.date_posted || job.published_at)}</div>
      </div>
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

    const skillsText = ((job.skills?.length ? job.skills : job.skill_tags) || []).slice(0, 15).map(skillLabel).filter(Boolean).join(", ") || "Skills not specified";

    detail.innerHTML = `<p class="eyebrow">${escapeHtml(label(job.opportunity_type))} · ${escapeHtml(workModeLabel(job.work_mode))}</p>
      <h2>${escapeHtml(job.title)}</h2><p class="detail-company">${escapeHtml(job.company_name || "Company not specified")}</p>
      <p class="detail-location">${escapeHtml(job.location?.display_name || "Location not specified")} · ${formatDate(job.date_posted)}</p>
      <div class="detail-grid">
        <div><span>Salary</span><strong>${escapeHtml(formatSalary(job.salary))}</strong></div>
        <div><span>Recruiting term</span><strong>${escapeHtml(job.recruiting_term?.display_name || "Term not specified")}</strong></div>
        <div class="detail-grid-full"><span>Skills</span><strong>${escapeHtml(skillsText)}</strong></div>
      </div>
      <div class="detail-description">${job.description ? renderMarkdown(job.description) : "<p>No detailed description is available for this job.</p>"}</div>
      <div class="job-ai-actions">
        <button class="btn-ai-action" type="button" data-ai-target="tab-ats">ATS Review ↗</button>
        <button class="btn-ai-action" type="button" data-ai-target="tab-interview">Mock Interview ↗</button>
        <button class="btn-ai-action" type="button" data-ai-target="tab-cover-letter">Cover Letter ↗</button>
      </div>
      <div class="detail-actions" style="margin-top: 16px;">${job.sources?.map((source) => `<a class="primary-button" href="${escapeHtml(source.direct_url || source.url)}" target="_blank" rel="noreferrer">View Application Link ↗</a>`).join("") || ""}</div>`;
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
  if (targetTabId === "tab-tracker") {
    fetchTrackerData();
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

document.querySelectorAll(".nav-tab").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

// =========================================================
// GLOBAL AI SETTINGS & LOCALSTORAGE
// =========================================================

const SETTINGS_STORAGE_KEY = "wecanfindintern_ai_settings_v3";

const aiSettings = {
  selectedModel: "DeepSeek:deepseek-v4-flash",
  deepseekKey: "",
  geminiKey: "",
  openaiKey: "",
};

const EYE_SVG_OPEN = `<svg class="eye-svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>`;
const EYE_SVG_OFF = `<svg class="eye-svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>`;

function syncKeyFieldWithModel() {
  const modelVal = $("#setting-selected-model")?.value || "DeepSeek:deepseek-v4-flash";
  const [provider] = modelVal.split(":");
  const keyInput = $("#setting-api-key");
  const keyLabel = $("#setting-key-label");
  const keyHint = $("#setting-key-hint");

  if (!keyInput || !keyLabel) return;

  if (provider === "DeepSeek") {
    keyLabel.textContent = "DeepSeek API Key";
    keyInput.placeholder = "sk-... (leave blank to use server default)";
    keyInput.value = aiSettings.deepseekKey || "";
    if (keyHint) keyHint.textContent = "Leave blank to use the backend server's default DEEPSEEK_API_KEY.";
  } else if (provider === "OpenAI") {
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
  if (modelEl) modelEl.value = aiSettings.selectedModel || "DeepSeek:deepseek-v4-flash";
  syncKeyFieldWithModel();
}

function saveSettings() {
  const modelVal = $("#setting-selected-model")?.value || "DeepSeek:deepseek-v4-flash";
  const [provider] = modelVal.split(":");
  const currentKey = $("#setting-api-key")?.value.trim() || "";

  aiSettings.selectedModel = modelVal;
  if (provider === "DeepSeek") {
    aiSettings.deepseekKey = currentKey;
  } else if (provider === "OpenAI") {
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
  const [provider, modelName] = (aiSettings.selectedModel || "DeepSeek:deepseek-v4-flash").split(":");
  let apiKey = null;
  if (provider === "DeepSeek") {
    apiKey = aiSettings.deepseekKey || null;
  } else if (provider === "OpenAI") {
    apiKey = aiSettings.openaiKey || null;
  } else {
    apiKey = aiSettings.geminiKey || null;
  }
  return {
    provider: provider,
    model_name: modelName || (provider === "DeepSeek" ? "deepseek-v4-flash" : provider === "OpenAI" ? "gpt-4o-mini" : "gemini-3.7-flash"),
    api_key: apiKey,
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
// APPLICATION TRACKER CONTROLLER (LOCAL POSTGRESQL PERSISTENCE)
// =========================================================

/* Legacy card-only tracker retained in history; replaced by the scalable workspace below.

async function fetchTrackerData() {
  trackerState.loading = true;
  try {
    const res = await fetch("/api/v1/tracker");
    if (!res.ok) throw new Error("Failed to load tracker data");
    const data = await res.json();
    trackerState.applications = data.items || [];
    trackerState.stats = data.stats || {};
    trackerState.trackedJobIds = new Set(
      trackerState.applications
        .filter((app) => app.job_id)
        .map((app) => app.job_id)
    );
    renderTrackerStats();
    renderTrackerBoard();
    updateBookmarkButtons();
  } catch (err) {
    console.error("Tracker fetch error:", err);
  } finally {
    trackerState.loading = false;
  }
}

function renderTrackerStats() {
  const s = trackerState.stats;
  if ($("#stat-total")) $("#stat-total").textContent = (s.total || 0).toLocaleString();
  if ($("#stat-interested")) $("#stat-interested").textContent = (s.interested_count || 0).toLocaleString();
  if ($("#stat-applied")) $("#stat-applied").textContent = (s.applied_count || 0).toLocaleString();
  if ($("#stat-interview")) $("#stat-interview").textContent = (s.interview_count || 0).toLocaleString();
  if ($("#stat-offer")) $("#stat-offer").textContent = (s.offer_count || 0).toLocaleString();
  if ($("#stat-rate")) $("#stat-rate").textContent = `${s.response_rate_percent || 0}%`;

  if ($("#count-interested")) $("#count-interested").textContent = s.interested_count || 0;
  if ($("#count-applied")) $("#count-applied").textContent = s.applied_count || 0;
  if ($("#count-interview")) $("#count-interview").textContent = s.interview_count || 0;
  if ($("#count-offer")) $("#count-offer").textContent = s.offer_count || 0;
  if ($("#count-rejected")) $("#count-rejected").textContent = s.rejected_count || 0;
}

function renderTrackerBoard() {
  const stages = ["interested", "applied", "interview", "offer", "rejected"];
  const grouped = {
    interested: [],
    applied: [],
    interview: [],
    offer: [],
    rejected: [],
  };

  trackerState.applications.forEach((app) => {
    const st = app.stage || "interested";
    if (grouped[st]) {
      grouped[st].push(app);
    }
  });

  stages.forEach((st) => {
    const colList = $(`#cards-${st}`);
    if (!colList) return;
    const items = grouped[st];
    if (!items.length) {
      colList.innerHTML = `<div class="kanban-empty-hint">No opportunities in this stage</div>`;
      return;
    }

    colList.innerHTML = items
      .map((app) => {
        const salaryBadge = app.salary_text
          ? `<span class="tracker-card-salary">${escapeHtml(app.salary_text)}</span>`
          : "";
        const notePreview = app.notes
          ? `<div class="tracker-notes-preview" data-app-id="${app.id}" title="Click to edit notes">${escapeHtml(app.notes)}</div>`
          : `<div class="tracker-notes-preview" style="color:var(--muted); font-style:italic;" data-app-id="${app.id}">+ Add notes / interview details</div>`;

        return `
        <div class="tracker-card" data-app-id="${app.id}">
          <div class="tracker-card-header">
            <span class="tracker-card-company">${escapeHtml(app.company_name)}</span>
            <h4 class="tracker-card-title">${escapeHtml(app.title)}</h4>
            <div class="tracker-card-meta">
              <span>${escapeHtml(app.location_text || "Location not specified")}</span>
              ${app.work_mode ? `<span>· ${escapeHtml(workModeLabel(app.work_mode))}</span>` : ""}
            </div>
            ${salaryBadge}
          </div>

          <div class="tracker-stage-select-wrap">
            <select class="tracker-stage-select" data-app-id="${app.id}" aria-label="Change stage">
              <option value="interested" ${app.stage === "interested" ? "selected" : ""}>Interested</option>
              <option value="applied" ${app.stage === "applied" ? "selected" : ""}>Applied</option>
              <option value="interview" ${app.stage === "interview" ? "selected" : ""}>Interview</option>
              <option value="offer" ${app.stage === "offer" ? "selected" : ""}>Offer</option>
              <option value="rejected" ${app.stage === "rejected" ? "selected" : ""}>Refused</option>
            </select>
          </div>

          ${notePreview}

          <div class="tracker-card-actions">
            <div class="tracker-ai-links">
              <button type="button" class="tracker-icon-btn btn-ai-ats" data-app-id="${app.id}" title="Evaluate in ATS Review">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><path d="m9 15 2 2 4-4"></path></svg>
                <span>ATS</span>
              </button>
              <button type="button" class="tracker-icon-btn btn-ai-interview" data-app-id="${app.id}" title="Practice in Interview Coach">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path><path d="M19 10v2a7 7 0 0 1-14 0v-2"></path></svg>
                <span>Mock</span>
              </button>
              <button type="button" class="tracker-icon-btn btn-ai-cl" data-app-id="${app.id}" title="Generate Cover Letter">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
                <span>Letter</span>
              </button>
            </div>
            <button type="button" class="tracker-icon-btn btn-delete" data-app-id="${app.id}" title="Remove from Pipeline">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
            </button>
          </div>
        </div>`;
      })
      .join("");
  });

  $$(".tracker-stage-select").forEach((sel) => {
    sel.addEventListener("change", async (e) => {
      const appId = e.target.dataset.appId;
      const newStage = e.target.value;
      await updateApplicationStage(appId, newStage);
    });
  });

  $$(".tracker-notes-preview").forEach((preview) => {
    preview.addEventListener("click", () => {
      const appId = preview.dataset.appId;
      openTrackerNotesDialog(appId);
    });
  });

  $$(".btn-ai-ats").forEach((btn) => {
    btn.addEventListener("click", () => {
      const app = trackerState.applications.find((a) => a.id === btn.dataset.appId);
      if (!app) return;
      $("#ats-jd-text").value = `${app.title} at ${app.company_name}\n\nLocation: ${app.location_text || "Unspecified"}\n\nNotes:\n${app.notes || ""}`;
      switchTab("tab-ats");
    });
  });

  $$(".btn-ai-interview").forEach((btn) => {
    btn.addEventListener("click", () => {
      const app = trackerState.applications.find((a) => a.id === btn.dataset.appId);
      if (!app) return;
      $("#interview-jd-text").value = `${app.title} at ${app.company_name}\n\nLocation: ${app.location_text || "Unspecified"}\n\nNotes:\n${app.notes || ""}`;
      switchTab("tab-interview");
    });
  });

  $$(".btn-ai-cl").forEach((btn) => {
    btn.addEventListener("click", () => {
      const app = trackerState.applications.find((a) => a.id === btn.dataset.appId);
      if (!app) return;
      $("#cl-jd-text").value = `${app.title} at ${app.company_name}\n\nLocation: ${app.location_text || "Unspecified"}\n\nNotes:\n${app.notes || ""}`;
      switchTab("tab-cover-letter");
    });
  });

  $$(".tracker-icon-btn.btn-delete").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const appId = btn.dataset.appId;
      await deleteTrackedApplication(appId);
    });
  });
}

function updateBookmarkButtons() {
  $$(".job-bookmark-btn").forEach((btn) => {
    const jId = btn.dataset.jobId;
    const isSaved = trackerState.trackedJobIds?.has(jId);
    btn.classList.toggle("saved", Boolean(isSaved));
    btn.title = isSaved ? "Tracked in Pipeline" : "Bookmark / Track Job";
    btn.innerHTML = isSaved
      ? `<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>`
      : `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>`;
  });
}

async function updateApplicationStage(appId, newStage) {
  try {
    const res = await fetch(`/api/v1/tracker/${appId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stage: newStage }),
    });
    if (!res.ok) throw new Error("Failed to update stage");
    await fetchTrackerData();
  } catch (err) {
    alert(`Could not update application stage: ${err.message}`);
  }
}

function openTrackerNotesDialog(appId) {
  const app = trackerState.applications.find((a) => a.id === appId);
  if (!app) return;
  $("#notes-dialog-app-id").value = app.id;
  $("#notes-dialog-job-title").textContent = `${app.title}`;
  $("#notes-dialog-company").textContent = `${app.company_name} · ${app.location_text || "Unspecified"}`;
  $("#notes-dialog-stage").value = app.stage || "interested";
  $("#notes-dialog-text").value = app.notes || "";

  const dialog = $("#tracker-notes-dialog");
  dialog?.showModal();
  syncDialogScrollLock();
}

async function saveTrackerNotes() {
  const appId = $("#notes-dialog-app-id").value;
  const stage = $("#notes-dialog-stage").value;
  const notes = $("#notes-dialog-text").value.trim();

  if (!appId) return;
  try {
    const res = await fetch(`/api/v1/tracker/${appId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stage, notes }),
    });
    if (!res.ok) throw new Error("Failed to save notes");
    $("#tracker-notes-dialog")?.close();
    await fetchTrackerData();
  } catch (err) {
    alert(`Save failed: ${err.message}`);
  }
}

async function deleteTrackedApplication(appId) {
  if (!confirm("Are you sure you want to remove this job from your pipeline?")) return;
  try {
    const res = await fetch(`/api/v1/tracker/${appId}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Failed to delete application");
    $("#tracker-notes-dialog")?.close();
    await fetchTrackerData();
  } catch (err) {
    alert(`Delete failed: ${err.message}`);
  }
}

async function toggleBookmarkJob(jobId, jobContext = null) {
  const existing = trackerState.applications.find((a) => a.job_id === jobId);
  if (existing) {
    // Already in pipeline, prompt to view or delete
    switchTab("tab-tracker");
    return;
  }

  // Add to tracker as Interested
  let payload = {
    job_id: jobId,
    company_name: "Company",
    title: "Internship Role",
    stage: "interested",
  };

  if (jobContext) {
    payload.company_name = jobContext.company || "Company";
    payload.title = jobContext.title || "Internship Role";
    payload.location_text = jobContext.location || "";
  } else {
    const card = $(`.job-card[data-id="${jobId}"]`);
    if (card) {
      payload.title = card.querySelector("h3")?.textContent || "Internship Role";
      payload.company_name = card.querySelector(".company-name")?.textContent || "Company";
      payload.location_text = card.querySelector(".job-location")?.textContent || "";
    }
  }

  try {
    const res = await fetch("/api/v1/tracker", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error("Failed to track job");
    await fetchTrackerData();
  } catch (err) {
    alert(`Could not add to pipeline: ${err.message}`);
  }
}

// Custom Job Dialog Wiring
$("#btn-open-custom-job")?.addEventListener("click", () => {
  $("#custom-job-form").reset();
  $("#custom-job-error").hidden = true;
  const dialog = $("#custom-job-dialog");
  dialog?.showModal();
  syncDialogScrollLock();
});
$("#close-custom-job-dialog")?.addEventListener("click", () => $("#custom-job-dialog")?.close());
$("#btn-cancel-custom-job")?.addEventListener("click", () => $("#custom-job-dialog")?.close());
$("#custom-job-dialog")?.addEventListener("click", (e) => {
  if (e.target === $("#custom-job-dialog")) $("#custom-job-dialog")?.close();
});

$("#custom-job-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const company = $("#custom-company").value.trim();
  const title = $("#custom-title").value.trim();
  const location = $("#custom-location").value.trim();
  const workMode = $("#custom-work-mode").value;
  const stage = $("#custom-stage").value;
  const salary = $("#custom-salary").value.trim();
  const url = $("#custom-url").value.trim();
  const notes = $("#custom-notes").value.trim();

  if (!company || !title) return;

  try {
    const res = await fetch("/api/v1/tracker", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        company_name: company,
        title: title,
        location_text: location || null,
        work_mode: workMode || null,
        stage: stage || "interested",
        salary_text: salary || null,
        job_url: url || null,
        notes: notes || null,
      }),
    });
    if (!res.ok) throw new Error("Failed to save custom application");
    $("#custom-job-dialog")?.close();
    await fetchTrackerData();
  } catch (err) {
    const errBox = $("#custom-job-error");
    if (errBox) {
      errBox.textContent = err.message;
      errBox.hidden = false;
    }
  }
});

// Tracker Notes Dialog Wiring
$("#close-tracker-notes-dialog")?.addEventListener("click", () => $("#tracker-notes-dialog")?.close());
$("#btn-cancel-tracker-notes")?.addEventListener("click", () => $("#tracker-notes-dialog")?.close());
$("#btn-save-tracker-notes")?.addEventListener("click", saveTrackerNotes);
$("#btn-delete-tracked-app")?.addEventListener("click", () => {
  const appId = $("#notes-dialog-app-id").value;
  if (appId) deleteTrackedApplication(appId);
});
$("#tracker-notes-dialog")?.addEventListener("click", (e) => {
  if (e.target === $("#tracker-notes-dialog")) $("#tracker-notes-dialog")?.close();
});

*/

// =========================================================
// SCALABLE APPLICATION TRACKER WORKSPACE
// =========================================================

const TRACKER_FILTER_KEY = "wecan_tracker_filters_v2";
const trackerStageLabels = {
  interested: "Interested",
  applied: "Applied",
  interview: "Interviewing",
  offer: "Offers",
  rejected: "Refused",
};

function trackerFiltersFromControls() {
  const [sort, direction] = ($("#tracker-sort")?.value || "updated:desc").split(":");
  return {
    query: $("#tracker-query")?.value.trim() || "",
    stage: trackerState.stageFilter || "",
    work_mode: "",
    priority: "",
    archived: "all",
    date_from: "",
    date_to: "",
    sort,
    direction,
  };
}

function buildTrackerParams() {
  const filters = trackerFiltersFromControls();
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => value && params.set(key, value));
  params.set("page", String(trackerState.page));
  params.set("page_size", String(trackerState.pageSize));
  return params;
}

function syncTrackerFilterState() {
  const filters = trackerFiltersFromControls();
  localStorage.setItem(TRACKER_FILTER_KEY, JSON.stringify({ query: filters.query, stage: filters.stage, sort: filters.sort, direction: filters.direction }));
  const url = new URL(window.location.href);
  ["tq", "tstage", "tmode", "tpriority", "tarchive", "tfrom", "tto", "tsort"].forEach((key) => url.searchParams.delete(key));
  if (filters.query) url.searchParams.set("tq", filters.query);
  if (filters.stage) url.searchParams.set("tstage", filters.stage);
  if (`${filters.sort}:${filters.direction}` !== "updated:desc") url.searchParams.set("tsort", `${filters.sort}:${filters.direction}`);
  history.replaceState({}, "", url);
  const exportParams = new URLSearchParams();
  if (filters.query) exportParams.set("query", filters.query);
  if (filters.stage) exportParams.set("stage", filters.stage);
  const exportLink = $("#tracker-export");
  if (exportLink) exportLink.href = `/api/v1/tracker/export.csv?${exportParams}`;
}

function restoreTrackerFilters() {
  let stored = {};
  try { stored = JSON.parse(localStorage.getItem(TRACKER_FILTER_KEY) || "{}"); } catch (_) { stored = {}; }
  const url = new URL(window.location.href);
  trackerState.stageFilter = url.searchParams.get("tstage") ?? stored.stage ?? "";
  if ($("#tracker-query")) $("#tracker-query").value = url.searchParams.get("tq") ?? stored.query ?? "";
  if ($("#tracker-sort")) $("#tracker-sort").value = url.searchParams.get("tsort") ?? `${stored.sort || "updated"}:${stored.direction || "desc"}`;
}

async function fetchTrackerData({ keepSelection = false } = {}) {
  const requestVersion = ++trackerState.requestVersion;
  trackerState.loading = true;
  $("#tracker-result-count").textContent = "Loading…";
  try {
    syncTrackerFilterState();
    const params = buildTrackerParams();
    const [listRes, bookmarksRes] = await Promise.all([
      fetch(`/api/v1/tracker?${params}`),
      fetch("/api/v1/tracker/bookmarks"),
    ]);
    if (!listRes.ok) throw new Error("Failed to load applications");
    const data = await listRes.json();
    if (requestVersion !== trackerState.requestVersion) return;
    trackerState.applications = data.items || [];
    trackerState.stats = data.stats || {};
    trackerState.total = data.total || 0;
    trackerState.totalPages = data.total_pages || 0;
    if (bookmarksRes.ok) {
      const bookmarks = await bookmarksRes.json();
      trackerState.trackedJobs = new Map(bookmarks.map((item) => [item.job_id, item]));
      trackerState.trackedJobIds = new Set(trackerState.trackedJobs.keys());
    }
    if (!keepSelection) trackerState.selectedIds.clear();
    renderTrackerStats();
    renderTrackerList();
    renderTrackerPagination();
    updateBookmarkButtons();
  } catch (error) {
    $("#tracker-result-count").textContent = error.message;
    console.error("Tracker fetch error:", error);
  } finally {
    if (requestVersion === trackerState.requestVersion) trackerState.loading = false;
  }
}

function renderTrackerStats() {
  const s = trackerState.stats;
  const values = {
    "#stat-total": s.total,
    "#stat-interested": s.interested_count,
    "#stat-applied": s.applied_count,
    "#stat-interview": s.interview_count,
    "#stat-offer": s.offer_count,
    "#stat-rejected": s.rejected_count,
  };
  Object.entries(values).forEach(([selector, value]) => { if ($(selector)) $(selector).textContent = Number(value || 0).toLocaleString(); });
  if ($("#stat-rate")) $("#stat-rate").textContent = `${s.response_rate_percent || 0}%`;

  const currentStage = trackerState.stageFilter || "";
  $$(".tracker-stat-card[data-stat-stage]").forEach((card) => {
    card.classList.toggle("active", (card.dataset.statStage ?? "") === currentStage);
  });
}

function trackerDate(value, fallback = "—") {
  if (!value) return fallback;
  const date = new Date(value.length === 10 ? `${value}T12:00:00` : value);
  return Number.isNaN(date.getTime()) ? fallback : new Intl.DateTimeFormat("en-CA", { month: "short", day: "numeric", year: "numeric" }).format(date);
}

function stageBadge(stage) {
  return `<span class="tracker-stage-badge stage-${escapeHtml(stage)}">${escapeHtml(trackerStageLabels[stage] || label(stage))}</span>`;
}

const trackerSourceLabels = {
  wecanfindintern: "WecanFindIntern",
  linkedin: "LinkedIn",
  indeed: "Indeed",
  waterloo_work: "WaterlooWork",
  other: "Other",
};

function renderTrackerList() {
  const body = $("#tracker-table-body");
  if (!body) return;
  const items = trackerState.applications;
  $("#tracker-empty").hidden = Boolean(items.length);
  const titleMap = {
    "": "All applications",
    interested: "Interested",
    applied: "Applied",
    interview: "Interviewing",
    offer: "Offers",
    rejected: "Refused",
  };
  $("#tracker-results-title").textContent = titleMap[trackerState.stageFilter] || "All applications";
  $("#tracker-result-count").textContent = `${trackerState.total.toLocaleString()} record${trackerState.total === 1 ? "" : "s"}`;
  body.innerHTML = items.map((app) => {
    const nextAction = app.next_step || (app.follow_up_at ? `Follow up ${trackerDate(app.follow_up_at)}` : "Add next action");
    const priority = app.priority === "high" ? `<span class="priority-mark" title="High priority">●</span>` : "";
    return `<tr class="tracker-row" data-app-id="${app.id}">
      <td class="select-col"><input class="tracker-row-check" data-app-id="${app.id}" type="checkbox" aria-label="Select ${escapeHtml(app.title)}" ${trackerState.selectedIds.has(app.id) ? "checked" : ""} /></td>
      <td><div class="tracker-role-cell"><strong>${priority}${escapeHtml(app.company_name)}</strong><span>${escapeHtml(app.title)}</span><small>${escapeHtml(trackerSourceLabels[app.source] || "Other")}</small></div></td>
      <td><select class="tracker-inline-stage stage-${escapeHtml(app.stage)}" data-app-id="${app.id}" aria-label="Change stage for ${escapeHtml(app.title)}"><option value="interested" ${app.stage === "interested" ? "selected" : ""}>Interested</option><option value="applied" ${app.stage === "applied" ? "selected" : ""}>Applied</option><option value="interview" ${app.stage === "interview" ? "selected" : ""}>Interview</option><option value="offer" ${app.stage === "offer" ? "selected" : ""}>Offer</option><option value="rejected" ${app.stage === "rejected" ? "selected" : ""}>Refused</option></select></td>
      <td><div class="tracker-muted-cell"><span>${escapeHtml(app.location_text || "Unspecified")}</span><small>${escapeHtml(workModeLabel(app.work_mode))}</small></div></td>
      <td><input class="tracker-inline-date" data-app-id="${app.id}" type="date" value="${toDateInput(app.applied_at)}" aria-label="Applied date for ${escapeHtml(app.title)}" /></td>
      <td><button class="tracker-next-action" data-app-id="${app.id}" type="button">${escapeHtml(nextAction)}</button></td>
      <td><span title="${escapeHtml(new Date(app.updated_at).toLocaleString())}">${escapeHtml(formatRelativeTime(app.updated_at))}</span></td>
      <td><button class="tracker-row-menu" data-app-id="${app.id}" type="button" aria-label="Open application">›</button></td>
    </tr>`;
  }).join("");
  updateBulkBar();
}

function renderTrackerBoard() {
  const grouped = { interested: [], applied: [], interview: [], offer: [], rejected: [] };
  trackerState.applications.forEach((app) => grouped[app.stage]?.push(app));
  Object.entries(grouped).forEach(([stage, items]) => {
    const list = $(`#cards-${stage}`);
    if (!list) return;
    if (!items.length) { list.innerHTML = `<div class="kanban-empty-hint">No applications on this page</div>`; return; }
    list.innerHTML = items.slice(0, 20).map((app) => `<article class="tracker-card compact-tracker-card" data-app-id="${app.id}" tabindex="0">
      <div><span class="tracker-card-company">${escapeHtml(app.company_name)}</span><h4 class="tracker-card-title">${escapeHtml(app.title)}</h4></div>
      <div class="tracker-card-meta"><span>${escapeHtml(app.location_text || "Unspecified")}</span>${app.priority === "high" ? `<span class="priority-label">High priority</span>` : ""}</div>
      <div class="compact-card-footer"><span>${escapeHtml(app.next_step || trackerDate(app.follow_up_at, "No next action"))}</span><button type="button" data-app-id="${app.id}" aria-label="Open details">›</button></div>
    </article>`).join("");
  });
}

function renderTrackerPagination() {
  const start = trackerState.total ? (trackerState.page - 1) * trackerState.pageSize + 1 : 0;
  const end = Math.min(trackerState.page * trackerState.pageSize, trackerState.total);
  $("#tracker-page-summary").textContent = `${start}–${end} of ${trackerState.total.toLocaleString()}`;
  $("#tracker-page-prev").disabled = trackerState.page <= 1;
  $("#tracker-page-next").disabled = trackerState.page >= trackerState.totalPages;
  $("#tracker-select-page").checked = trackerState.applications.length > 0 && trackerState.applications.every((app) => trackerState.selectedIds.has(app.id));
}

function updateBulkBar() {
  const count = trackerState.selectedIds.size;
  $("#tracker-bulk-bar").hidden = count === 0;
  $("#tracker-selected-count").textContent = count.toLocaleString();
}

function setTrackerView(view) {
  trackerState.view = view;
  localStorage.setItem("wecan_tracker_view", view);
  $$("[data-tracker-view]").forEach((button) => button.classList.toggle("active", button.dataset.trackerView === view));
  ["list", "board", "analytics"].forEach((name) => { $(`#tracker-${name}-view`).hidden = name !== view; });
  if (view === "analytics") loadTrackerAnalytics();
}

async function loadTrackerAttention({ show = true } = {}) {
  const panel = $("#attention-panel");
  if (show) panel.hidden = false;
  const response = await fetch("/api/v1/tracker/needs-attention?limit=30");
  if (!response.ok) return;
  const data = await response.json();
  trackerState.attentionItems = data.items || [];
  $("#attention-list").innerHTML = trackerState.attentionItems.length ? trackerState.attentionItems.map((item) => `<button class="attention-item attention-${escapeHtml(item.reason_type)}" data-app-id="${item.application.id}" type="button"><span class="attention-dot"></span><span><strong>${escapeHtml(item.application.company_name)} · ${escapeHtml(item.application.title)}</strong><small>${escapeHtml(item.reason)}${item.due_at ? ` · ${trackerDate(item.due_at)}` : ""}</small></span><span>›</span></button>`).join("") : `<p class="attention-clear">Everything is up to date.</p>`;
}

function analyticsBars(title, items, labelKey = "label") {
  const max = Math.max(1, ...items.map((item) => item.count));
  return `<section class="analytics-card"><h3>${escapeHtml(title)}</h3><div class="analytics-bars">${items.length ? items.map((item) => `<div class="analytics-bar-row"><span>${escapeHtml(item[labelKey] || label(item.stage))}</span><div><i style="width:${Math.max(4, item.count / max * 100)}%"></i></div><strong>${item.count}</strong></div>`).join("") : `<p class="tracker-view-note">Not enough data yet.</p>`}</div></section>`;
}

async function loadTrackerAnalytics() {
  const root = $("#tracker-analytics");
  if (!root) return;
  root.innerHTML = `<div class="loading-state"><div class="spinner"></div><span>Building your insights…</span></div>`;
  const response = await fetch("/api/v1/tracker/analytics");
  if (!response.ok) { root.innerHTML = `<div class="notice error">Could not load insights.</div>`; return; }
  const data = await response.json();
  trackerState.analytics = data;
  root.innerHTML = `<section class="analytics-card analytics-highlight"><span>Average response time</span><strong>${data.average_days_to_response == null ? "—" : `${data.average_days_to_response} days`}</strong><p>From application to the first recorded outcome.</p></section><section class="analytics-card conversion-card"><h3>Conversion health</h3><div><span>Application → interview</span><strong>${data.application_to_interview_percent}%</strong></div><div><span>Interview → offer</span><strong>${data.interview_to_offer_percent}%</strong></div></section>${analyticsBars("Pipeline mix", data.stages, "stage")}${analyticsBars("Applications by week", data.weekly_applications.map((item) => ({ label: trackerDate(item.week_start), count: item.count })))}${analyticsBars("Top companies", data.top_companies)}${analyticsBars("Top locations", data.top_locations)}${analyticsBars("Application sources", data.top_sources)}${analyticsBars("Role categories", data.top_categories)}`;
}

function toDateInput(value) { return value ? String(value).slice(0, 10) : ""; }
function toDateTimeInput(value) {
  if (!value) return "";
  const date = new Date(value);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

function syncTrackerLinkActions() {
  const url = $("#drawer-url").value.trim();
  $("#drawer-copy-job").disabled = !url;
  $("#drawer-open-job").href = url || "#";
  $("#drawer-open-job").hidden = !url;
}

async function openTrackerDrawer(appId) {
  let app = trackerState.applications.find((item) => item.id === appId);
  if (!app) {
    const response = await fetch(`/api/v1/tracker/${appId}`);
    if (!response.ok) return;
    app = await response.json();
  }
  $("#drawer-app-id").value = app.id;
  $("#drawer-title").textContent = app.title;
  $("#drawer-company").textContent = `${app.company_name} · ${app.location_text || "Location unspecified"}`;
  const isPlatformJob = app.origin_type === "platform_bookmark";
  $("#drawer-origin-notice").textContent = isPlatformJob
    ? "Bookmarked from WecanFindIntern · Job information is synced from the platform and is read-only."
    : "External application · You can edit both the job information and tracking fields.";
  $("#drawer-origin-notice").classList.toggle("platform-origin", isPlatformJob);
  const fields = {
    "#drawer-stage": app.stage, "#drawer-priority": app.priority || "normal",
    "#drawer-applied-at": toDateInput(app.applied_at),
    "#drawer-follow-up": toDateTimeInput(app.follow_up_at), "#drawer-source": app.source || "other",
    "#drawer-url": app.job_url || "", "#drawer-jd-input": app.job_description || "",
  };
  Object.entries(fields).forEach(([selector, value]) => { $(selector).value = value; });
  $("#drawer-jd-readonly").innerHTML = app.job_description ? renderMarkdown(app.job_description) : "<p>No job description is available.</p>";
  $("#drawer-jd-readonly").hidden = !isPlatformJob;
  $("#drawer-jd-input").hidden = isPlatformJob;
  $$("#tracker-detail-drawer .job-content-field input, #tracker-detail-drawer .job-content-field select, #tracker-detail-drawer .job-content-field textarea").forEach((field) => {
    field.disabled = isPlatformJob;
  });
  $("#tracker-detail-drawer").dataset.originType = app.origin_type;
  syncTrackerLinkActions();
  $("#tracker-detail-drawer").classList.add("open");
  $("#tracker-detail-drawer").setAttribute("aria-hidden", "false");
  $("#tracker-drawer-backdrop").hidden = false;
  document.body.classList.add("modal-open");
  await loadTrackerTimeline(app.id);
}

function closeTrackerDrawer() {
  $("#tracker-detail-drawer").classList.remove("open");
  $("#tracker-detail-drawer").setAttribute("aria-hidden", "true");
  $("#tracker-drawer-backdrop").hidden = true;
  document.body.classList.remove("modal-open");
}

async function saveTrackerDrawer() {
  const appId = $("#drawer-app-id").value;
  if (!appId) return;
  const dateIso = (selector) => $(selector).value ? new Date(`${$(selector).value}T12:00:00`).toISOString() : null;
  const dateTimeIso = (selector) => $(selector).value ? new Date($(selector).value).toISOString() : null;
  const payload = {
    stage: $("#drawer-stage").value, priority: $("#drawer-priority").value,
    applied_at: dateIso("#drawer-applied-at"),
    follow_up_at: dateTimeIso("#drawer-follow-up"),
  };
  if ($("#tracker-detail-drawer").dataset.originType === "custom") {
    Object.assign(payload, {
      job_description: $("#drawer-jd-input").value.trim() || null,
      source: $("#drawer-source").value,
      job_url: $("#drawer-url").value.trim() || null,
    });
  }
  const response = await fetch(`/api/v1/tracker/${appId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  if (!response.ok) { alert("Could not save this application."); return; }
  const saved = await response.json();
  $("#drawer-title").textContent = saved.title;
  $("#drawer-company").textContent = `${saved.company_name} · ${saved.location_text || "Location unspecified"}`;
  $("#drawer-save-status").hidden = false;
  setTimeout(() => { $("#drawer-save-status").hidden = true; }, 1600);
  await fetchTrackerData({ keepSelection: true });
  await loadTrackerTimeline(appId);
}

async function loadTrackerTimeline(appId) {
  const root = $("#tracker-timeline");
  root.innerHTML = `<p class="tracker-view-note">Loading progress…</p>`;
  const response = await fetch(`/api/v1/tracker/${appId}/events`);
  if (!response.ok) {
    root.innerHTML = `<p class="tracker-view-note">Progress is temporarily unavailable.</p>`;
    return;
  }
  const events = await response.json();
  root.innerHTML = events.length
    ? events.map((event) => `<article class="timeline-item"><span class="timeline-marker event-${escapeHtml(event.event_type)}"></span><div><strong>${escapeHtml(event.title)}</strong>${event.details ? `<p>${escapeHtml(event.details)}</p>` : ""}<time>${trackerDate(event.occurred_at)} · ${escapeHtml(formatRelativeTime(event.occurred_at))}</time></div></article>`).join("")
    : `<p class="tracker-view-note">No progress recorded yet.</p>`;
}

async function bulkUpdateTracker(payload) {
  const ids = [...trackerState.selectedIds];
  if (!ids.length) return;
  const response = await fetch("/api/v1/tracker/bulk", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids, ...payload }) });
  if (!response.ok) { alert("Bulk update failed."); return; }
  trackerState.selectedIds.clear();
  await fetchTrackerData();
}

function showTrackerUndo(message, undoAction) {
  let toast = $("#tracker-undo-toast");
  if (!toast) {
    document.body.insertAdjacentHTML("beforeend", `<div id="tracker-undo-toast" class="tracker-undo-toast" hidden><span></span><button type="button">Undo</button></div>`);
    toast = $("#tracker-undo-toast");
  }
  clearTimeout(trackerState.undoTimer);
  toast.querySelector("span").textContent = message;
  const button = toast.querySelector("button");
  button.onclick = async () => { toast.hidden = true; await undoAction(); };
  toast.hidden = false;
  trackerState.undoTimer = setTimeout(() => { toast.hidden = true; }, 6000);
}

async function deleteTrackedApplication(appId) {
  if (!confirm("Delete this application and its activity permanently?")) return;
  const response = await fetch(`/api/v1/tracker/${appId}`, { method: "DELETE" });
  if (!response.ok) { alert("Delete failed."); return; }
  closeTrackerDrawer();
  await fetchTrackerData();
}

function updateBookmarkButtons() {
  $$(".job-bookmark-btn").forEach((btn) => {
    const saved = trackerState.trackedJobIds.has(btn.dataset.jobId);
    btn.classList.toggle("saved", saved);
    btn.title = saved ? "Tracked in workspace" : "Track this job";
    btn.setAttribute("aria-pressed", String(saved));
    btn.innerHTML = saved
      ? `<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>`
      : `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>`;
  });
}

async function toggleBookmarkJob(jobId) {
  const existing = trackerState.trackedJobs.get(jobId);
  if (existing) {
    switchTab("tab-tracker");
    openTrackerDrawer(existing.application_id);
    return;
  }
  const buttons = $$(`.job-bookmark-btn[data-job-id="${jobId}"]`);
  buttons.forEach((button) => { button.disabled = true; });
  try {
    const response = await fetch(`/api/v1/tracker/bookmarks/${jobId}`, { method: "PUT" });
    if (!response.ok) throw new Error("Could not add this job to your tracker.");
    const application = await response.json();
    const trackedState = { job_id: jobId, application_id: application.id, stage: application.stage };
    trackerState.trackedJobs.set(jobId, trackedState);
    trackerState.trackedJobIds.add(jobId);
    updateBookmarkButtons();
    await fetchTrackerData();
  } catch (error) {
    alert(error.message);
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

let trackerSearchTimer;
function trackerFiltersChanged() {
  trackerState.page = 1;
  clearTimeout(trackerSearchTimer);
  trackerSearchTimer = setTimeout(() => fetchTrackerData(), 250);
}

restoreTrackerFilters();

$("#tracker-query")?.addEventListener("input", trackerFiltersChanged);
$("#tracker-sort")?.addEventListener("change", trackerFiltersChanged);
$$(".tracker-stat-card[data-stat-stage]").forEach((card) => {
  card.addEventListener("click", () => {
    trackerState.stageFilter = card.dataset.statStage ?? "";
    trackerState.page = 1;
    trackerFiltersChanged();
  });
});
$("#tracker-page-prev")?.addEventListener("click", () => { if (trackerState.page > 1) { trackerState.page--; fetchTrackerData(); } });
$("#tracker-page-next")?.addEventListener("click", () => { if (trackerState.page < trackerState.totalPages) { trackerState.page++; fetchTrackerData(); } });
$("#tracker-page-size")?.addEventListener("change", (event) => { trackerState.pageSize = Number(event.target.value); trackerState.page = 1; fetchTrackerData(); });

$("#tracker-table-body")?.addEventListener("click", (event) => {
  const check = event.target.closest(".tracker-row-check");
  if (check) { check.checked ? trackerState.selectedIds.add(check.dataset.appId) : trackerState.selectedIds.delete(check.dataset.appId); updateBulkBar(); renderTrackerPagination(); return; }
  if (event.target.closest("select,input")) return;
  const target = event.target.closest("[data-app-id]");
  if (target) openTrackerDrawer(target.dataset.appId);
});
$("#tracker-table-body")?.addEventListener("change", async (event) => {
  const stage = event.target.closest(".tracker-inline-stage");
  const appliedDate = event.target.closest(".tracker-inline-date");
  if (!stage && !appliedDate) return;
  const appId = (stage || appliedDate).dataset.appId;
  const payload = stage
    ? { stage: stage.value }
    : { applied_at: appliedDate.value ? new Date(`${appliedDate.value}T12:00:00`).toISOString() : null };
  const response = await fetch(`/api/v1/tracker/${appId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  if (!response.ok) { alert("Inline update failed."); await fetchTrackerData({ keepSelection: true }); return; }
  await fetchTrackerData({ keepSelection: true });
});
$("#tracker-board-view")?.addEventListener("click", (event) => { const target = event.target.closest("[data-app-id]"); if (target) openTrackerDrawer(target.dataset.appId); });
$("#attention-list")?.addEventListener("click", (event) => { const target = event.target.closest("[data-app-id]"); if (target) openTrackerDrawer(target.dataset.appId); });
$("#tracker-select-page")?.addEventListener("change", (event) => { trackerState.applications.forEach((app) => event.target.checked ? trackerState.selectedIds.add(app.id) : trackerState.selectedIds.delete(app.id)); renderTrackerList(); renderTrackerPagination(); });
$("#tracker-clear-selection")?.addEventListener("click", () => { trackerState.selectedIds.clear(); renderTrackerList(); renderTrackerPagination(); });
$("#tracker-bulk-stage")?.addEventListener("change", (event) => { if (event.target.value) bulkUpdateTracker({ stage: event.target.value }); event.target.value = ""; });
$("#tracker-bulk-priority")?.addEventListener("change", (event) => { if (event.target.value) bulkUpdateTracker({ priority: event.target.value }); event.target.value = ""; });
$("#tracker-bulk-delete")?.addEventListener("click", async () => {
  const ids = [...trackerState.selectedIds]; if (!ids.length || !confirm(`Delete ${ids.length} applications permanently?`)) return;
  const response = await fetch("/api/v1/tracker/bulk", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids }) });
  if (response.ok) { trackerState.selectedIds.clear(); fetchTrackerData(); }
});

$("#close-tracker-drawer")?.addEventListener("click", closeTrackerDrawer);
$("#tracker-drawer-backdrop")?.addEventListener("click", closeTrackerDrawer);
$("#save-tracker-drawer")?.addEventListener("click", saveTrackerDrawer);
$("#drawer-url")?.addEventListener("input", syncTrackerLinkActions);
$("#drawer-copy-job")?.addEventListener("click", async (event) => {
  const url = $("#drawer-url").value.trim();
  if (!url) { event.target.textContent = "No link"; setTimeout(() => { event.target.textContent = "Copy"; }, 1200); return; }
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(url);
    else throw new Error("Clipboard API unavailable");
    event.target.textContent = "Copied";
  } catch (_) {
    const fallback = document.createElement("textarea");
    fallback.value = url; fallback.style.position = "fixed"; fallback.style.opacity = "0";
    document.body.appendChild(fallback); fallback.select();
    const copied = document.execCommand("copy"); fallback.remove();
    event.target.textContent = copied ? "Copied" : "Copy failed";
  }
  setTimeout(() => { event.target.textContent = "Copy"; }, 1200);
});
$("#btn-delete-tracked-app")?.addEventListener("click", () => { const id = $("#drawer-app-id").value; if (id) deleteTrackedApplication(id); });
async function bulkUpdateTrackerForIds(ids, payload) {
  const response = await fetch("/api/v1/tracker/bulk", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids, ...payload }) });
  if (response.ok) await fetchTrackerData();
}
$("#btn-open-custom-job")?.addEventListener("click", () => { $("#custom-job-form").reset(); $("#custom-job-error").hidden = true; $("#custom-job-dialog")?.showModal(); syncDialogScrollLock(); });
$("#close-custom-job-dialog")?.addEventListener("click", () => $("#custom-job-dialog")?.close());
$("#btn-cancel-custom-job")?.addEventListener("click", () => $("#custom-job-dialog")?.close());
$("#custom-job-dialog")?.addEventListener("click", (event) => { if (event.target === $("#custom-job-dialog")) $("#custom-job-dialog")?.close(); });
$("#custom-job-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    company_name: $("#custom-company").value.trim(), title: $("#custom-title").value.trim(),
    location_text: $("#custom-location").value.trim() || null, work_mode: $("#custom-work-mode").value || null,
    stage: $("#custom-stage").value, salary_text: $("#custom-salary").value.trim() || null,
    priority: $("#custom-priority").value, source: $("#custom-source").value,
    applied_at: $("#custom-applied-at").value ? new Date(`${$("#custom-applied-at").value}T12:00:00`).toISOString() : null,
    follow_up_at: $("#custom-follow-up").value ? new Date($("#custom-follow-up").value).toISOString() : null,
    next_step: $("#custom-next-step").value.trim() || null, job_url: $("#custom-url").value.trim() || null,
    job_description: $("#custom-job-description").value.trim() || null,
  };
  const response = await fetch("/api/v1/tracker", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  if (!response.ok) { const box = $("#custom-job-error"); box.textContent = "Could not save this application."; box.hidden = false; return; }
  $("#custom-job-dialog")?.close(); await fetchTrackerData();
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
  const bookmarkBtn = event.target.closest(".job-bookmark-btn");
  if (bookmarkBtn) {
    event.stopPropagation();
    const jobId = bookmarkBtn.dataset.jobId;
    toggleBookmarkJob(jobId);
    return;
  }

  const card = event.target.closest(".job-card");
  if (card && !event.target.closest(".btn-ai-action") && !event.target.closest(".job-bookmark-btn")) {
    openJob(card.dataset.id);
  }

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
fetchTrackerData();
setupInfiniteScroll();
setupBackToTop();
