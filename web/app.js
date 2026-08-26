const state = { cursor: null, hasMore: false, facets: null };

const labels = {
  remote: "Remote", hybrid: "Hybrid", onsite: "On-site", unknown: "Not specified",
  internship: "Internship", co_op: "Co-op", new_grad: "New grad", regular: "Full-time",
  contract: "Contract", temporary: "Temporary", seasonal: "Seasonal", apprenticeship: "Apprenticeship",
  full_time: "Full-time", part_time: "Part-time", flexible: "Flexible",
  software_engineering: "Software engineering", data_ai: "Data & AI", cloud_devops: "Cloud & DevOps",
  cybersecurity: "Cybersecurity", product_design: "Product design", product_management: "Product management",
  hardware_embedded: "Hardware & embedded", research: "Research", engineering: "Engineering",
  business_operations: "Business operations", marketing_sales: "Marketing & sales", finance: "Finance",
};
const skillLabels = {
  cpp: "C++", csharp: "C#", dotnet: ".NET", javascript: "JavaScript", typescript: "TypeScript",
  nodejs: "Node.js", postgresql: "PostgreSQL", mongodb: "MongoDB", power_bi: "Power BI",
  scikit_learn: "scikit-learn", tensorflow: "TensorFlow", pytorch: "PyTorch", sql: "SQL",
  aws: "AWS", azure: "Azure", gcp: "GCP", git: "Git", agile: "Agile", jira: "Jira",
};

const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[char]));
const label = (value) => labels[value] || value || "Not specified";
const skillLabel = (value) => skillLabels[value] || label(value);
const workModeLabel = (value) => ({ remote: "Remote", hybrid: "Hybrid", onsite: "On-site", unknown: "Work mode not specified" }[value] || "Work mode not specified");

function setOptions(selector, items, emptyLabel) {
  const select = $(selector);
  const current = select.value;
  select.innerHTML = `<option value="">${emptyLabel}</option>`;
  for (const item of items || []) {
    const option = document.createElement("option");
    option.value = item.value;
    option.textContent = `${label(item.value)}  ·  ${item.count}`;
    select.appendChild(option);
  }
  if ([...select.options].some((option) => option.value === current)) select.value = current;
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
    country: "country", region: "region", city: "city", "work-mode": "work_mode",
    "opportunity-type": "opportunity_type", "schedule-type": "schedule_type",
    category: "category", skill: "skill",
  };
  for (const [elementId, param] of Object.entries(mappings)) {
    const value = $(`#${elementId}`).value;
    if (value) params.set(param, value);
  }
  if ($("#has-salary").checked) params.set("has_salary", "true");
  const salary = $("#annual-salary").value;
  if (salary) params.set("annual_salary_min", salary);
  params.set("limit", "20");
  return params;
}

function formatDate(value) {
  if (!value) return "Date not specified";
  return new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "short", day: "numeric" }).format(new Date(value));
}

function formatSalary(salary) {
  if (!salary || (salary.minimum == null && salary.maximum == null && salary.annualized_minimum == null && salary.annualized_maximum == null)) return "Salary not disclosed";
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
      <div class="job-tags"><span class="tag accent">${escapeHtml(label(job.opportunity_type))}</span>${tags.map((tag) => `<span class="tag">${escapeHtml(job.skill_tags?.includes(tag) ? skillLabel(tag) : label(tag))}</span>`).join("")}</div>
      <span class="salary">${escapeHtml(formatSalary(job.salary))}</span>
    </div>
  </article>`;
}

async function loadJobs({ append = false } = {}) {
  const list = $("#job-list");
  const error = $("#error");
  if (!append) { state.cursor = null; list.innerHTML = ""; $("#empty-state").hidden = true; }
  $("#result-status").textContent = append ? "Loading more…" : "Searching…";
  const params = readFilters();
  if (append && state.cursor) params.set("cursor", state.cursor);
  try {
    const response = await fetch(`/api/v1/jobs?${params}`);
    if (!response.ok) throw new Error(`Search failed (${response.status})`);
    const page = await response.json();
    list.insertAdjacentHTML("beforeend", page.items.map(renderJob).join(""));
    state.cursor = page.next_cursor;
    state.hasMore = page.has_more;
    $("#load-more").hidden = !page.has_more;
    $("#result-status").textContent = page.items.length ? `${page.items.length}${page.has_more ? "+" : ""} results` : "0 results";
    $("#empty-state").hidden = Boolean(list.children.length);
    error.hidden = true;
  } catch (requestError) {
    error.textContent = requestError.message;
    error.hidden = false;
    $("#result-status").textContent = "Load failed";
  }
}

async function loadFacets() {
  try {
    const response = await fetch("/api/v1/jobs/facets");
    if (!response.ok) throw new Error("facets unavailable");
    state.facets = await response.json();
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
  try {
    const response = await fetch(`/api/v1/jobs/${jobId}`);
    if (!response.ok) throw new Error("Could not load job details");
    const job = await response.json();
    detail.innerHTML = `<p class="eyebrow">${escapeHtml(label(job.opportunity_type))} · ${escapeHtml(workModeLabel(job.work_mode))}</p>
      <h2>${escapeHtml(job.title)}</h2><p class="detail-company">${escapeHtml(job.company_name || "Company not specified")}</p>
      <p class="detail-location">${escapeHtml(job.location?.display_name || "Location not specified")} · ${formatDate(job.date_posted)}</p>
      <div class="detail-grid"><div><span>Salary</span><strong>${escapeHtml(formatSalary(job.salary))}</strong></div><div><span>Skills</span><strong>${escapeHtml(((job.skills?.length ? job.skills : job.skill_tags) || []).slice(0, 8).map(skillLabel).join(", ") || "Skills not specified")}</strong></div></div>
      <div class="detail-description">${job.description ? escapeHtml(job.description).replace(/\n/g, "<br />") : "No detailed description is available for this job."}</div>
      <div class="detail-actions">${job.sources?.map((source) => `<a class="primary-button" href="${escapeHtml(source.direct_url || source.url)}" target="_blank" rel="noreferrer">View original job ↗</a>`).join("") || ""}</div>`;
  } catch (requestError) {
    detail.innerHTML = `<div class="notice error">${escapeHtml(requestError.message)}</div>`;
  }
}

$("#search-form").addEventListener("submit", (event) => { event.preventDefault(); loadJobs(); });
$("#load-more").addEventListener("click", () => loadJobs({ append: true }));
$("#refresh").addEventListener("click", () => loadJobs());
$("#clear-filters").addEventListener("click", () => {
  $("#search-form").reset(); $("#location").value = "";
  ["#country", "#region", "#city", "#work-mode", "#opportunity-type", "#schedule-type", "#category", "#skill"].forEach((id) => { $(id).value = ""; });
  loadJobs();
});
document.addEventListener("click", (event) => {
  const card = event.target.closest(".job-card");
  if (card) openJob(card.dataset.id);
  const quick = event.target.closest("[data-query]");
  if (quick) { $("#query").value = quick.dataset.query; loadJobs(); }
});
$("#close-dialog").addEventListener("click", () => $("#job-dialog").close());
$("#job-dialog").addEventListener("click", (event) => { if (event.target === $("#job-dialog")) $("#job-dialog").close(); });

loadFacets();
loadJobs();
