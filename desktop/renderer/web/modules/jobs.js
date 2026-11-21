import {
  $,
  formatRelativeTime,
  label,
  renderJobDetail,
  skillLabel,
  showToast,
  showFieldError,
  clearFieldError,
  workModeLabel,
} from "./helpers.js?v=20260901-error-dialog-minimal-v1";
import {
  bookmarkState,
  loadPublicBookmarks,
  toggleBookmarkJob,
} from "./bookmarks.js";
import {
  publicJobContext,
  setActiveJobContext,
} from "./job-context.js?v=20260831-jobboard-parity-v3";
import { setupPostingWorkspace } from "./posting-workspace.js";
import {
  renderMultiFilter,
  selectedMultiFilterValues,
  setMultiFilterSelections,
  setupFilterPopover,
  setupMultiFilterInteractions,
} from "./multi-filter.js?v=20260902-shared-filters-v1";
import { createDebouncedAction } from "./timing.js?v=20260902-shared-components-v1";

const state = {
  cursor: null,
  hasMore: false,
  loading: false,
  facets: null,
  totalCount: 0,
};
let jobsAbortController = null;
const workspace = setupPostingWorkspace($("#tab-jobs"), {
  loadDetail: loadPublicJobDetail,
  activateJob: job => setActiveJobContext(publicJobContext(job)),
});

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

function setOptions(elementId, items, placeholder) {
  const id = elementId.replace(/^#/, "");
  renderMultiFilter(elementId, items, {
    placeholder,
    getLabel: (item) => filterDisplayValue(id, item.value),
    formatCount: (item) => Number(item.count || 0).toLocaleString(),
  });
}

function setFilterSelections(elementId, values) {
  setMultiFilterSelections(`#${elementId}`, values);
}

document.querySelectorAll("#job-filters-sheet .multi-filter").forEach((root) => {
  setOptions(`#${root.id}`, [], root.dataset.placeholder || "All options");
});

function updateLocationOptions() {
  const facets = state.facets || {};
  setMapFacetOptions("country", facets.countries, "All countries");
  setMapFacetOptions("region", facets.regions, "All regions");
  setOptions("#city", facets.cities, "All cities");
}

function setMapFacetOptions(id, items = [], placeholder, values = selectedMultiFilterValues(`#${id}`)) {
  const missing = values.filter((value) => !items.some((item) => item.value === value));
  setOptions(`#${id}`, [...items, ...missing.map((value) => ({ value, count: 0 }))], placeholder);
  setFilterSelections(id, values);
}

function validHourlyRange(reveal = false) {
  const min = $("#hourly-min");
  const max = $("#hourly-max");
  let valid = true;
  for (const field of [min, max]) {
    let message = "";
    if (field.validity.badInput || field.validity.rangeUnderflow) message = "Enter a number of zero or more.";
    else if (field === max && max.value !== "" && min.valueAsNumber > max.valueAsNumber) message = "Maximum must be at least the minimum.";
    if (message) {
      if (reveal && valid) {
        const sheet = $("#job-filters-sheet");
        sheet.showPopover({ source: $("#open-job-filters") });
        sheet.dispatchEvent(new Event("wcfi:show-final-filter-group"));
      }
      showFieldError(field, message, { focus: reveal && valid });
      valid = false;
    } else clearFieldError(field);
  }
  return valid;
}

function readFilters() {
  const params = new URLSearchParams();
  const query = $("#query").value.trim();
  const location = $("#location").value.trim();
  if (query) params.set("query", query);
  if (location) params.set("location", location);
  for (const [elementId, param] of Object.entries(filterParamMap)) {
    selectedMultiFilterValues(`#${elementId}`).forEach((value) => params.append(param, value));
  }
  if ($("#has-salary").getAttribute("aria-checked") === "true") params.set("has_salary", "true");
  const hourlyMin = $("#hourly-min").value.trim();
  if (hourlyMin !== "") params.set("hourly_salary_min", hourlyMin);
  const hourlyMax = $("#hourly-max").value.trim();
  if (hourlyMax !== "") params.set("hourly_salary_max", hourlyMax);
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

function applyRegionFilter(countryCode, regionCode) {
  // The map includes regions with zero openings, which can be absent from the facets.
  setMapFacetOptions("country", state.facets?.countries, "All countries", [countryCode]);
  setMapFacetOptions("region", state.facets?.regions, "All regions", [`${regionCode},${countryCode}`]);
  loadJobs();
  $("#tab-jobs .results-scroll-content")?.scrollTo({ top: 0, behavior: "smooth" });
}

async function loadJobs({ append = false } = {}) {
  if (!validHourlyRange()) return;
  const list = $("#job-list");
  const loadingIndicator = $("#loading-indicator");
  const endOfResults = $("#end-of-results");

  if (append && (state.loading || !state.hasMore || !state.cursor)) return;
  if (!append) jobsAbortController?.abort();
  state.loading = true;

  if (!append) {
    state.cursor = null;
    list.innerHTML = "";
    $("#tab-jobs .results-scroll-content")?.scrollTo({ top: 0, behavior: "instant" });
    $("#empty-state").hidden = true;
    if (loadingIndicator) loadingIndicator.hidden = true;
    if (endOfResults) endOfResults.hidden = true;
    $("#result-status").textContent = "Searching…";
  } else {
    if (loadingIndicator) loadingIndicator.hidden = false;
    if (endOfResults) endOfResults.hidden = true;
  }

  const params = readFilters();
  const filterCount = [...params.keys()].filter((key) => !["query", "limit"].includes(key)).length;
  $("#posting-filter-label").textContent = filterCount ? `Filters (${filterCount})` : "Filters";
  const filtered = filterCount > 0 || params.has("query") || params.has("location");
  $("#results-title").textContent = filtered ? "Search results" : "Latest jobs";
  $("#reset-job-search").hidden = !filtered;
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
    if (controller.signal.aborted) return;
    const added = workspace.renderRows(page.items, append);
    state.cursor = page.next_cursor;
    state.hasMore = page.has_more;
    if (page.last_updated_at) {
      updateLastUpdatedBadge(page.last_updated_at);
    }
    if (!append) {
      state.totalCount = typeof page.total_count === "number" ? page.total_count : added;
    }
    const total = state.totalCount ?? 0;
    const formattedCount = total.toLocaleString("en-US");
    const countLabel = `${formattedCount} result${total === 1 ? "" : "s"}`;
    $("#result-status").textContent = countLabel;
    $("#facet-results-label").textContent = "Show " + countLabel;
    if (!list.children.length) {
      if (filtered) {
        workspace.showEmpty("No matching jobs", "Try a broader search term or remove a filter.", "Clear search", () => $("#clear-filters").click());
      } else {
        workspace.showEmpty("No public jobs yet", "New postings will appear here after the next job sync.");
      }
    }
    if (endOfResults) {
      endOfResults.hidden = Boolean(page.has_more || !list.children.length);
    }
  } catch (requestError) {
    if (requestError.name === "AbortError") return;
    if (append) {
      showToast("Could not load more jobs. Try refreshing the list.", { variant: "error" });
    } else {
      workspace.closeDetail(false);
      $("#result-status").textContent = "Unavailable";
      $("#facet-results-label").textContent = "Show results";
      workspace.showEmpty("Job feed unavailable", "The job service could not be reached. Try reloading the feed.", "Retry", () => loadJobs());
    }
  } finally {
    if (controller.signal.aborted) return;
    state.loading = false;
    if (loadingIndicator) loadingIndicator.hidden = true;
  }
}

const debouncedLoadJobs = createDebouncedAction(() => loadJobs());

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

async function loadPublicJobDetail(jobId, signal) {
  const response = await fetch(`/api/v1/jobs/${jobId}`, { signal });
  if (!response.ok) throw new Error("Could not load job details");
  const job = await response.json();
  if (!signal) setActiveJobContext(publicJobContext(job));
  return { job, html: renderJobDetail(job, { isSaved: bookmarkState.publicJobs.has(job.id) }) };
}

setupFilterPopover($("#job-filters-sheet"), $("#open-job-filters"));

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

// =========================================================
// SEARCH AND FILTER EVENT LISTENERS
// =========================================================

$("#reset-job-search")?.addEventListener("click", () => $("#clear-filters").click());
$("#search-form")?.addEventListener("submit", (event) => { event.preventDefault(); if (validHourlyRange(true)) loadJobs(); });
$("#refresh")?.addEventListener("click", () => loadJobs());
$("#show-filter-results").addEventListener("click", () => {
  if (!validHourlyRange(true)) return;
  loadJobs();
  $("#job-filters-sheet").hidePopover();
});
$("#location").addEventListener("input", () => debouncedLoadJobs(280));
$("#location")?.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    if (!validHourlyRange(true)) return;
    loadJobs();
    $("#job-filters-sheet").hidePopover();
  }
});
setupMultiFilterInteractions("#job-filters-sheet", {
  closeOnOutsideClick: true,
  onClear: () => debouncedLoadJobs(100),
});
$("#job-filters-sheet").addEventListener("change", (event) => {
  if (event.target.id !== "hourly-min" && event.target.id !== "hourly-max" && !event.target.classList.contains("dual-range-slider")) {
    debouncedLoadJobs(150);
  }
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
  if (event.target.value !== "" && !isNaN(val) && val >= 0) {
    const clamped = Math.max(minSliderVal, Math.min(val, 100));
    $("#hourly-slider-max").value = clamped;
  } else if (!event.target.value.trim()) {
    $("#hourly-slider-max").value = 100;
  }
  updateSliderFill();
  debouncedLoadJobs(300);
});
$("#clear-filters")?.addEventListener("click", () => {
  $("#search-form").reset();
  Object.keys(filterParamMap).forEach((id) => setFilterSelections(id, []));
  $("#hourly-min").value = "";
  $("#hourly-max").value = "";
  $("#hourly-slider-min").value = 0;
  $("#hourly-slider-max").value = 100;
  updateSliderFill();
  loadJobs();
});
document.addEventListener("click", (event) => {
  document.querySelectorAll(".posting-actions details[open]").forEach((menu) => {
    if (!menu.contains(event.target)) menu.open = false;
  });
  const bookmarkBtn = event.target.closest(".job-bookmark-btn");
  if (bookmarkBtn) {
    event.stopPropagation();
    toggleBookmarkJob(bookmarkBtn.dataset.jobId);
  }
});

export {
  state,
  loadJobs,
  loadFacets,
  loadPublicJobDetail,
  updateSliderFill,
  applyRegionFilter,
};
