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
import {
  closeJobDetailPane,
  openJobDetailPane,
} from "./job-detail-pane.js?v=20260902-shared-components-v1";
import {
  renderMultiFilter,
  selectedMultiFilterValues,
  setMultiFilterSelections,
  setMultiFilterSheetOpen,
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
    selectedMultiFilterValues(`#${elementId}`).forEach((value) => params.append(param, value));
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
  $("#tab-jobs .results-scroll-content")?.scrollTo({ top: 0, behavior: "smooth" });
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

async function loadPublicJobDetail(jobId) {
  const response = await fetch(`/api/v1/jobs/${jobId}`);
  if (!response.ok) throw new Error("Could not load job details");
  const job = await response.json();
  setActiveJobContext(publicJobContext(job));
  return {
    job,
    html: renderJobDetail(job, {
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
    }),
  };
}

async function openJob(jobId) {
  await openJobDetailPane({
    paneSelector: "#public-job-detail-pane",
    detailSelector: "#public-job-detail",
    cardsSelector: "#job-list .job-card",
    selectedId: jobId,
    getCardId: (card) => card.dataset.id,
    loadDetail: loadPublicJobDetail,
    errorTitle: "Job details unavailable",
  });
}

function setFilterSheetOpen(open) {
  setMultiFilterSheetOpen("#job-filters-sheet", "#job-filters-backdrop", open);
}

function closePublicJobDetail() {
  closeJobDetailPane({
    paneSelector: "#public-job-detail-pane",
    selectedCardsSelector: "#job-list .job-card.selected",
  });
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
  const scrollRoot = $("#tab-jobs .results-scroll-content");
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
setupMultiFilterInteractions("#job-filters-sheet", {
  closeOnOutsideClick: true,
  onClear: () => debouncedLoadJobs(100),
});
$(".filters-panel")?.addEventListener("change", (event) => {
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
  loadPublicJobDetail,
  openJob,
  updateSliderFill,
  setupBackToTop,
  applyRegionFilter,
};
