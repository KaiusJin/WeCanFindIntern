import {
  $,
  escapeHtml,
  formatDate,
  formatRelativeTime,
  formatSalary,
  label,
  renderMarkdown,
  skillLabel,
  workModeLabel,
} from "./helpers.js";
import { syncDialogScrollLock } from "./settings.js";
import { toggleBookmarkJob, trackerState } from "./tracker.js";

const state = {
  cursor: null,
  hasMore: false,
  loading: false,
  facets: null,
  totalCount: 0,
  activeJobContext: null,
};
let jobsAbortController = null;
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

  const controller = new AbortController();
  jobsAbortController?.abort();
  jobsAbortController = controller;

  try {
    const response = await fetch(`/api/v1/jobs?${params}`, { signal: controller.signal });
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
    if (requestError.name === "AbortError") return;
    error.textContent = requestError.message;
    error.hidden = false;
    $("#result-status").textContent = "Load failed";
  } finally {
    if (controller.signal.aborted) return;
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
// SEARCH AND FILTER EVENT LISTENERS
// =========================================================

$("#search-form").addEventListener("submit", (event) => { event.preventDefault(); loadJobs(); });
$("#refresh").addEventListener("click", () => loadJobs());
$(".filters-panel").addEventListener("change", (event) => {
  if (event.target.id !== "hourly-min" && event.target.id !== "hourly-max" && !event.target.classList.contains("dual-range-slider")) {
    debouncedLoadJobs(150);
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

export {
  state,
  loadJobs,
  loadFacets,
  openJob,
  updateSliderFill,
  setupInfiniteScroll,
  setupBackToTop,
};
