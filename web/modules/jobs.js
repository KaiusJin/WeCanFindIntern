import {
  $,
  escapeHtml,
  formatDate,
  formatRelativeTime,
  formatSalary,
  label,
  renderJobCard,
  renderJobDetail,
  skillLabel,
  showErrorDialog,
  workModeLabel,
} from "./helpers.js?v=20260901-error-dialog-minimal-v1";
import {
  bookmarkState,
  loadPublicBookmarks,
  toggleBookmarkJob,
  updateBookmarkButtons,
} from "./bookmarks.js";
import {
  publicJobContext,
  setActiveJobContext,
} from "./job-context.js?v=20260831-jobboard-parity-v3";

const state = {
  cursor: null,
  hasMore: false,
  loading: false,
  facets: null,
  totalCount: 0,
};
let jobsAbortController = null;

const filterParamMap = {
  "recruiting-term": "recruiting_term",
  country: "country",
  region: "region",
  city: "city",
  "work-mode": "work_mode",
  "opportunity-type": "opportunity_type",
  "schedule-type": "schedule_type",
  category: "category",
  skill: "skill",
};

function filterDisplayValue(elementId, value) {
  if (elementId === "region") {
    const [region, country] = value.split(",");
    return country ? `${region},${country}` : region;
  }
  if (elementId === "work-mode") return workModeLabel(value);
  if (elementId === "skill") return skillLabel(value);
  if (["opportunity-type", "schedule-type", "category"].includes(elementId)) {
    return label(value);
  }
  return value;
}

function selectedFilterValues(elementId) {
  return [...document.querySelectorAll(`#${elementId} input[type="checkbox"]:checked`)]
    .map((input) => input.value);
}

function updateMultiFilterSummary(root) {
  const selected = [...root.querySelectorAll('input[type="checkbox"]:checked')];
  const summary = root.querySelector(".multi-filter-summary");
  const clearButton = root.querySelector(".multi-filter-clear");
  if (!summary) return;
  summary.textContent = selected.length === 0
    ? root.dataset.placeholder
    : selected.length === 1
      ? selected[0].dataset.label
      : `${selected.length} selected`;
  if (clearButton) clearButton.hidden = selected.length === 0;
  root.classList.toggle("has-selection", selected.length > 0);
}

function setOptions(elementId, items, placeholder) {
  const root = $(elementId);
  const id = elementId.replace(/^#/, "");
  const selected = new Set(selectedFilterValues(id));
  root.dataset.placeholder = placeholder;
  root.innerHTML = `
    <button class="multi-filter-trigger" type="button" aria-expanded="false">
      <span class="multi-filter-summary">${escapeHtml(placeholder)}</span>
      <span class="multi-filter-chevron" aria-hidden="true">⌄</span>
    </button>
    <div class="multi-filter-menu" hidden>
      <div class="multi-filter-menu-head">
        <span>Select one or more</span>
        <button class="multi-filter-clear" type="button" hidden>Clear</button>
      </div>
      <div class="multi-filter-options">
        ${(items || []).map((item, index) => {
          const display = filterDisplayValue(id, item.value);
          return `<label class="multi-filter-option" for="${id}-option-${index}">
            <input id="${id}-option-${index}" type="checkbox" value="${escapeHtml(item.value)}" data-label="${escapeHtml(display)}"${selected.has(item.value) ? " checked" : ""} />
            <span>${escapeHtml(display)}</span>
            <small>${Number(item.count || 0).toLocaleString()}</small>
          </label>`;
        }).join("") || '<p class="multi-filter-empty">No options available</p>'}
      </div>
    </div>`;
  updateMultiFilterSummary(root);
}

function setFilterSelections(elementId, values) {
  const root = $(`#${elementId}`);
  const selected = new Set(values);
  root?.querySelectorAll('input[type="checkbox"]').forEach((input) => {
    input.checked = selected.has(input.value);
  });
  if (root) updateMultiFilterSummary(root);
}

document.querySelectorAll(".multi-filter").forEach((root) => {
  setOptions(`#${root.id}`, [], root.dataset.placeholder || "All options");
});

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
  if (location) params.set("location", location);
  for (const [elementId, param] of Object.entries(filterParamMap)) {
    selectedFilterValues(elementId).forEach((value) => params.append(param, value));
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
  const recruitingTerm = job.recruiting_term?.display_name;
  const isSaved = bookmarkState.publicJobs.has(job.id);
  return renderJobCard(job, {
    isSaved,
    primaryTag: label(job.opportunity_type),
    secondaryTags: recruitingTerm ? [{ label: recruitingTerm, className: "term-tag" }] : [],
  });
}

function applyRegionFilter(countryCode, regionCode) {
  setFilterSelections("country", [countryCode]);
  setFilterSelections("region", [`${regionCode},${countryCode}`]);
  loadJobs();
  $("#tab-jobs .results-panel")?.scrollTo({ top: 0, behavior: "smooth" });
}

async function loadJobs({ append = false } = {}) {
  const list = $("#job-list");
  const loadingIndicator = $("#loading-indicator");
  const endOfResults = $("#end-of-results");

  if (append && (state.loading || !state.hasMore || !state.cursor)) return;
  if (!append) jobsAbortController?.abort();
  state.loading = true;

  if (!append) {
    state.cursor = null;
    list.innerHTML = "";
    $("#tab-jobs .results-panel")?.scrollTo({ top: 0, behavior: "instant" });
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
  jobsAbortController = controller;

  try {
    const bookmarksPromise = append
      ? Promise.resolve()
      : loadPublicBookmarks().catch((error) => {
        console.warn("Tracker bookmarks unavailable:", error);
      });
    const response = await fetch(`/api/v1/jobs?${params}`, { signal: controller.signal });
    if (!response.ok) throw new Error(`Search failed (${response.status})`);
    const page = await response.json();
    await bookmarksPromise;
    const existingIds = new Set(
      [...list.querySelectorAll(".job-card")].map((card) => card.dataset.id),
    );
    const newItems = page.items.filter((job) => !existingIds.has(job.id));
    list.insertAdjacentHTML("beforeend", newItems.map(renderJob).join(""));
    // Normalize freshly inserted bookmark buttons through the same renderer
    // used after bookmark mutations. This prevents the initial SVG from
    // looking different until the user clicks the button.
    updateBookmarkButtons();
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
  } catch (requestError) {
    if (requestError.name === "AbortError") return;
    $("#result-status").textContent = "—";
    showErrorDialog(requestError, { title: "Jobs could not be loaded" });
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
    setOptions("#work-mode", state.facets.work_modes, "All work modes");
    updateLocationOptions();
  } catch (_) {
    // The results page remains usable if facets are temporarily unavailable.
  }
}

async function openJob(jobId) {
  const pane = $("#public-job-detail-pane");
  const detail = $("#public-job-detail");
  detail.innerHTML = `<p class="loading-detail">Loading job details…</p>`;
  pane.classList.add("open", "has-selection");
  pane.setAttribute("aria-hidden", "false");
  document.querySelectorAll("#job-list .job-card").forEach((card) => {
    card.classList.toggle("selected", card.dataset.id === String(jobId));
  });
  try {
    const response = await fetch(`/api/v1/jobs/${jobId}`);
    if (!response.ok) throw new Error("Could not load job details");
    const job = await response.json();
    setActiveJobContext(publicJobContext(job));

    detail.innerHTML = renderJobDetail(job, {
      eyebrow: `${label(job.opportunity_type)} · ${workModeLabel(job.work_mode)}`,
      company: job.company_name,
      location: job.location?.display_name,
      meta: formatDate(job.date_posted),
      skills: job.source_skills?.length ? job.source_skills : job.skill_tags,
      facts: [
        { label: "Salary", value: formatSalary(job.salary) },
        { label: "Recruiting term", value: job.recruiting_term?.display_name || "Term not specified" },
      ],
      links: (job.sources || []).map((source) => source.direct_url || source.url),
    });
  } catch (requestError) {
    pane.classList.remove("open", "has-selection");
    pane.setAttribute("aria-hidden", "true");
    showErrorDialog(requestError, { title: "Job details unavailable" });
  }
}

function setFilterSheetOpen(open) {
  const sheet = $("#job-filters-sheet");
  const backdrop = $("#job-filters-backdrop");
  if (!sheet || !backdrop) return;
  sheet.classList.toggle("open", open);
  sheet.setAttribute("aria-hidden", String(!open));
  backdrop.hidden = !open;
  document.body.classList.toggle("filter-sheet-open", open);
}

function closePublicJobDetail() {
  const pane = $("#public-job-detail-pane");
  pane?.classList.remove("open");
  pane?.setAttribute("aria-hidden", "true");
  document.querySelectorAll("#job-list .job-card.selected").forEach((card) => card.classList.remove("selected"));
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

function setupBackToTop() {
  const backToTopBtn = $("#back-to-top");
  if (!backToTopBtn) return;
  const scrollRoot = $("#tab-jobs .results-panel");
  scrollRoot?.addEventListener(
    "scroll",
    () => {
      backToTopBtn.hidden = scrollRoot.scrollTop < 500;
    },
    { passive: true },
  );
  backToTopBtn.addEventListener("click", () => {
    scrollRoot?.scrollTo({ top: 0, behavior: "smooth" });
  });
}

// =========================================================
// SEARCH AND FILTER EVENT LISTENERS
// =========================================================

$("#search-form")?.addEventListener("submit", (event) => { event.preventDefault(); loadJobs(); });
$("#refresh")?.addEventListener("click", () => loadJobs());
$(".filters-panel")?.addEventListener("click", (event) => {
  const trigger = event.target.closest(".multi-filter-trigger");
  if (trigger) {
    const root = trigger.closest(".multi-filter");
    document.querySelectorAll(".multi-filter.open").forEach((other) => {
      if (other === root) return;
      other.classList.remove("open");
      other.querySelector(".multi-filter-menu").hidden = true;
      other.querySelector(".multi-filter-trigger").setAttribute("aria-expanded", "false");
    });
    const willOpen = !root.classList.contains("open");
    root.classList.toggle("open", willOpen);
    root.querySelector(".multi-filter-menu").hidden = !willOpen;
    trigger.setAttribute("aria-expanded", String(willOpen));
    return;
  }
  const clear = event.target.closest(".multi-filter-clear");
  if (clear) {
    const root = clear.closest(".multi-filter");
    root.querySelectorAll('input[type="checkbox"]').forEach((input) => {
      input.checked = false;
    });
    updateMultiFilterSummary(root);
    debouncedLoadJobs(100);
  }
});
$(".filters-panel")?.addEventListener("change", (event) => {
  const multiFilter = event.target.closest(".multi-filter");
  if (multiFilter) updateMultiFilterSummary(multiFilter);
  if (event.target.id !== "hourly-min" && event.target.id !== "hourly-max" && !event.target.classList.contains("dual-range-slider")) {
    debouncedLoadJobs(150);
  }
});
document.addEventListener("click", (event) => {
  if (event.target.closest(".multi-filter")) return;
  document.querySelectorAll(".multi-filter.open").forEach((root) => {
    root.classList.remove("open");
    root.querySelector(".multi-filter-menu").hidden = true;
    root.querySelector(".multi-filter-trigger").setAttribute("aria-expanded", "false");
  });
});
$("#hourly-slider-min")?.addEventListener("input", (event) => {
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
$("#hourly-slider-max")?.addEventListener("input", (event) => {
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
$("#hourly-min")?.addEventListener("input", (event) => {
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
$("#hourly-max")?.addEventListener("input", (event) => {
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
$("#clear-filters")?.addEventListener("click", () => {
  $("#search-form").reset(); $("#location").value = "";
  Object.keys(filterParamMap).forEach((id) => setFilterSelections(id, []));
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
  if (card && !card.classList.contains("ww-job-card") && !event.target.closest(".btn-ai-action") && !event.target.closest(".job-bookmark-btn")) {
    openJob(card.dataset.id);
  }

  const quick = event.target.closest("[data-query], [data-term]");
  if (quick) {
    if (quick.dataset.query) $("#query").value = quick.dataset.query;
    if (quick.dataset.term) setFilterSelections("recruiting-term", [quick.dataset.term]);
    loadJobs();
  }
});
$("#open-job-filters")?.addEventListener("click", () => setFilterSheetOpen(true));
$("#close-job-filters")?.addEventListener("click", () => setFilterSheetOpen(false));
$("#job-filters-backdrop")?.addEventListener("click", () => setFilterSheetOpen(false));
$("#close-public-job-detail")?.addEventListener("click", closePublicJobDetail);
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  setFilterSheetOpen(false);
  if (window.matchMedia("(max-width: 900px)").matches) closePublicJobDetail();
});

export {
  state,
  loadJobs,
  loadFacets,
  openJob,
  updateSliderFill,
  setupBackToTop,
  applyRegionFilter,
};
