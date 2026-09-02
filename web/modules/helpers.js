const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => document.querySelectorAll(selector);
function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

function errorMessage(error, fallback = "An unexpected error occurred.") {
  if (typeof error === "string" && error.trim()) return error.trim();
  if (error?.message && String(error.message).trim()) return String(error.message).trim();
  if (error?.detail && typeof error.detail === "string") return error.detail.trim();
  return fallback;
}

async function responseErrorMessage(response, fallback = "The request could not be completed.") {
  const text = await response.text().catch(() => "");
  let payload = null;
  try { payload = text ? JSON.parse(text) : null; } catch (_) { payload = null; }
  const detail = payload?.detail ?? payload?.error ?? payload?.message;
  let reason = "";
  if (typeof detail === "string") {
    reason = detail.trim();
  } else if (Array.isArray(detail)) {
    reason = detail.map((issue) => {
      const path = Array.isArray(issue?.loc)
        ? issue.loc.filter((part) => part !== "body").join(".")
        : "";
      const message = issue?.msg || "Invalid value";
      return path ? `${path}: ${message}` : message;
    }).join("\n");
  } else if (text && !text.trim().startsWith("<")) {
    reason = text.trim();
  }
  const status = response?.status ? `HTTP ${response.status}` : "Request failed";
  return reason ? `${fallback}\n\n${reason}` : `${fallback}\n\nThe server returned ${status} without an explanation.`;
}

function ensureAppMessageDialog() {
  let dialog = $("#app-message-dialog");
  if (dialog) return dialog;
  document.body.insertAdjacentHTML("beforeend", `
    <dialog id="app-message-dialog" class="app-message-dialog" aria-labelledby="app-message-title" aria-describedby="app-message-detail">
      <div class="app-message-card">
        <button id="app-message-close" class="app-message-close" type="button" aria-label="Close">×</button>
        <h2 id="app-message-title">Unable to complete this action</h2>
        <div class="app-message-reason">
          <strong>Reason</strong>
          <p id="app-message-detail"></p>
        </div>
        <p id="app-message-guidance" class="app-message-guidance"></p>
        <button id="app-message-confirm" class="primary-button app-message-confirm" type="button">Got it</button>
      </div>
    </dialog>
  `);
  dialog = $("#app-message-dialog");
  const close = () => dialog.open && dialog.close();
  $("#app-message-close").addEventListener("click", close);
  $("#app-message-confirm").addEventListener("click", close);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) close();
  });
  return dialog;
}

function showAppMessage(message, {
  title = "Unable to complete this action",
  guidance = "Review the reason above, correct the information if needed, and try again.",
  variant = "error",
} = {}) {
  const dialog = ensureAppMessageDialog();
  dialog.dataset.variant = variant;
  $("#app-message-title").textContent = title;
  $("#app-message-detail").textContent = errorMessage(message);
  $("#app-message-guidance").textContent = guidance;
  if (!dialog.open) dialog.showModal();
  $("#app-message-confirm").focus();
}

function showErrorDialog(error, options = {}) {
  showAppMessage(error, { ...options, variant: "error" });
}

function showSuccessDialog(message, options = {}) {
  showAppMessage(message, {
    title: "Completed",
    guidance: "You can continue when you are ready.",
    ...options,
    variant: "success",
  });
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
  const canonicalLabels = {
    co_op: "Co-op",
    new_grad: "New grad",
    full_time: "Full-time",
    part_time: "Part-time",
  };
  if (canonicalLabels[value]) return canonicalLabels[value];
  return value.split("_").map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
}

function workModeLabel(value) {
  const map = { remote: "Remote", hybrid: "Hybrid", in_person: "In-person", onsite: "In-person", unknown: "Work mode not specified" };
  return map[value] || label(value);
}

function skillLabel(value) {
  if (!value) return "";
  const normalized = String(value).toLowerCase().replaceAll("_", " ");
  const map = {
    cpp: "C++", cplusplus: "C++", csharp: "C#", dotnet: ".NET", nodejs: "Node.js",
    nextjs: "Next.js", graphql: "GraphQL", grpc: "gRPC", html: "HTML", css: "CSS",
    javascript: "JavaScript", typescript: "TypeScript", postgresql: "PostgreSQL",
    sql_server: "SQL Server", bigquery: "BigQuery", github_actions: "GitHub Actions",
    gitlab_ci: "GitLab CI", argocd: "Argo CD", power_bi: "Power BI",
    scikit_learn: "scikit-learn", hugging_face: "Hugging Face", llm: "LLM",
    ai_agents: "AI Agents", llamaindex: "LlamaIndex", semantic_kernel: "Semantic Kernel",
    azure_openai: "Azure OpenAI", vertex_ai: "Vertex AI", vector_database: "Vector Database",
    vector_search: "Vector Search", prompt_engineering: "Prompt Engineering",
    function_calling: "Function Calling", tool_calling: "Tool Calling", mcp: "MCP",
    fine_tuning: "Fine-tuning", model_evaluation: "Model Evaluation",
    microsoft_office: "Microsoft Office", microsoft_365: "Microsoft 365", microsoft_word: "Microsoft Word",
    microsoft_teams: "Microsoft Teams", google_workspace: "Google Workspace", google_docs: "Google Docs",
    google_sheets: "Google Sheets", google_slides: "Google Slides", sharepoint: "SharePoint",
    onedrive: "OneDrive", macos: "macOS", ios: "iOS", android: "Android", red_hat: "Red Hat",
    chrome_os: "Chrome OS",
  };
  return map[String(value).toLowerCase()] || normalized.split(" ").map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
}

function renderJobTags({ primaryTag = "", secondaryTags = [], skillTags = [], category = "" } = {}) {
  const normalizedSkills = [...new Set(skillTags || [])].slice(0, 4);
  const tags = [...new Set([...normalizedSkills, category].filter(Boolean))];
  const primary = primaryTag
    ? `<span class="tag accent">${escapeHtml(primaryTag)}</span>`
    : "";
  const secondary = secondaryTags
    .filter(Boolean)
    .map((tag) => {
      const value = typeof tag === "string" ? { label: tag } : tag;
      return `<span class="tag ${value.className || ""}">${escapeHtml(value.label)}</span>`;
    })
    .join("");
  const skillSet = new Set(skillTags || []);
  const formatted = tags
    .map((tag) => `<span class="tag">${escapeHtml(skillSet.has(tag) ? skillLabel(tag) : label(tag))}</span>`)
    .join("");
  return `${primary}${secondary}${formatted}`;
}

function renderJobCard(job, {
  source = "job-board",
  isSaved = false,
  primaryTag = "",
  secondaryTags = [],
  dateText = "",
  boards = [],
  bookmarkIcon = "",
} = {}) {
  const isWaterlooWorks = source === "waterlooworks";
  const sourceId = isWaterlooWorks ? job.source_job_id : job.id;
  const companyName = job.company_name || job.organization || "Company not specified";
  const locationText = job.location?.display_name || job.location_text || "Location not specified";
  const defaultBookmarkIcon = isSaved
    ? `<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>`
    : `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2 2h10a2 2 0 0 1 2 2z"></path></svg>`;
  const bookmarkClass = isWaterlooWorks ? "ww-bookmark-btn" : "job-bookmark-btn";
  const bookmarkAttribute = isWaterlooWorks ? "data-source-job-id" : "data-job-id";
  const cardAttributes = isWaterlooWorks
    ? `data-source-job-id="${escapeHtml(sourceId)}" data-boards="${escapeHtml(boards.join(","))}" tabindex="0"`
    : `data-id="${escapeHtml(sourceId)}" tabindex="0"`;
  const visibleDate = dateText || formatDate(job.date_posted || job.published_at);
  const applicationDeadline = job.submitted_application_deadline || job.application_deadline;
  const cardSideMeta = isWaterlooWorks
    ? `<div class="ww-card-deadline"><span>Application due</span><strong>${escapeHtml(applicationDeadline || "Not specified")}</strong></div>`
    : `<div class="job-date">${escapeHtml(visibleDate)}</div>`;
  const footerMeta = isWaterlooWorks
    ? `<div class="ww-card-footer-meta"><span class="ww-card-job-id">Job ID ${escapeHtml(sourceId)}</span><span class="salary">${escapeHtml(formatSalary(job.salary))}</span></div>`
    : `<span class="salary">${escapeHtml(formatSalary(job.salary))}</span>`;

  return `<article class="job-card${isWaterlooWorks ? " ww-job-card" : ""}" ${cardAttributes}>
    <div class="job-card-main">
      <div class="company-mark">${escapeHtml(companyName.slice(0, 1).toUpperCase())}</div>
      <div class="job-copy">
        <h3>${escapeHtml(job.title)}</h3>
        <p class="company-name">${escapeHtml(companyName)}</p>
        <p class="job-location">${escapeHtml(locationText)} <span>·</span> ${escapeHtml(workModeLabel(job.work_mode))}</p>
      </div>
      <div style="display:flex; flex-direction:column; align-items:flex-end; gap:6px;">
        <button type="button" class="${bookmarkClass} ${isSaved ? "saved" : ""}" ${bookmarkAttribute}="${escapeHtml(sourceId)}" aria-pressed="${isSaved}" title="${isSaved ? "Tracked in Pipeline" : "Bookmark / Track Job"}>
          ${bookmarkIcon || defaultBookmarkIcon}
        </button>
        ${cardSideMeta}
      </div>
    </div>
    <div class="job-card-footer">
      <div class="job-tags">${renderJobTags({ primaryTag, secondaryTags, skillTags: job.skill_tags, category: job.job_category })}</div>
      ${footerMeta}
    </div>
  </article>`;
}

function buildJobContextText({
  title,
  company,
  location,
  workMode,
  recruitingTerm,
  sourceJobId,
  applicationDeadline,
  description,
} = {}) {
  const metadata = [
    `Location: ${location || "Unspecified"}`,
    `Work Mode: ${workModeLabel(workMode)}`,
  ];
  if (recruitingTerm) metadata.push(`Recruiting Term: ${recruitingTerm}`);
  if (sourceJobId) metadata.push(`WaterlooWorks Job ID: ${sourceJobId}`);
  if (applicationDeadline) metadata.push(`Application Deadline: ${applicationDeadline}`);
  return `${title || "Role"} at ${company || "Company"}\n\n${metadata.join("\n")}\n\nDescription:\n${description || ""}`;
}

function renderJobDetail(job, {
  eyebrow = "",
  company = "",
  location = "",
  meta = "",
  description = job.description,
  skills = job.source_skills?.length ? job.source_skills : job.skill_tags,
  facts = [],
  links = [],
  showAiActions = true,
} = {}) {
  const skillsText = (skills || [])
    .slice(0, 15)
    .map(skillLabel)
    .filter(Boolean)
    .join(", ") || "Skills not specified";
  const factMarkup = facts.map((fact) => `
    <div${fact.full ? ' class="detail-grid-full"' : ""}>
      <span>${escapeHtml(fact.label)}</span><strong>${escapeHtml(fact.value)}</strong>
    </div>`).join("");
  const aiActionButtons = showAiActions ? `
    <button class="btn-ai-action" type="button" data-ai-target="tab-ats-match">Job Match ↗</button>
    <button class="btn-ai-action" type="button" data-ai-target="tab-interview">Mock Interview ↗</button>
    <button class="btn-ai-action" type="button" data-ai-target="tab-cover-letter">Cover Letter ↗</button>
    <button class="btn-ai-action" type="button" data-ai-target="tab-agent">Ask AI Agent ↗</button>
  ` : "";
  const linkMarkup = links.filter(Boolean).map((url) =>
    `<a class="primary-button" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">Apply ↗</a>`,
  ).join("");
  const actionMarkup = aiActionButtons || linkMarkup
    ? `<div class="job-ai-actions">${aiActionButtons}${linkMarkup}</div>`
    : "";

  return `<p class="eyebrow">${escapeHtml(eyebrow)}</p>
    <h2>${escapeHtml(job.title)}</h2>
    <p class="detail-company">${escapeHtml(company || "Company not specified")}</p>
    <p class="detail-location">${escapeHtml(location || "Location not specified")}${meta ? ` · ${escapeHtml(meta)}` : ""}</p>
    <div class="detail-grid">
      ${factMarkup}
      <div class="detail-grid-full"><span>Skills</span><strong>${escapeHtml(skillsText)}</strong></div>
    </div>
    <div class="detail-description">${description ? renderMarkdown(description) : "<p>No detailed description is available for this job.</p>"}</div>
    ${actionMarkup}`;
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
  if (diffMin < 60) return `${diffMin}min ago`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour}h ago`;
  const diffDay = Math.floor(diffHour / 24);
  if (diffDay < 30) return `${diffDay}d ago`;
  const diffMonth = Math.floor(diffDay / 30);
  if (diffMonth < 12) return `${diffMonth}mo ago`;
  const diffYear = Math.floor(diffMonth / 12);
  return `${diffYear}y ago`;
}

function formatSalary(salary) {
  if (!salary || (salary.minimum == null && salary.maximum == null && salary.annualized_minimum == null && salary.annualized_maximum == null)) return "Salary not disclosed";
  const rawValues = [salary.minimum, salary.maximum].filter((value) => value != null).map(Number);
  if (rawValues.some((value) => !Number.isFinite(value) || value < 0)) return "Salary not disclosed";
  // Plausibility validation belongs to the backend salary domain.  The browser
  // only guards malformed transport values before formatting them.
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

async function fetchWithTimeout(url, options = {}, timeoutMs = 60000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  if (options.signal) {
    // Let callers abort (e.g. a Stop button); their abort rethrows as-is so
    // they can distinguish it from a timeout.
    if (options.signal.aborted) controller.abort();
    else options.signal.addEventListener("abort", () => controller.abort(), { once: true });
  }
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (error) {
    if (error.name === "AbortError") {
      if (options.signal?.aborted) throw error;
      throw new Error("Request timed out. Please try again.");
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function setupDropzone(dropzone, onFiles) {
  if (!dropzone) return;
  ["dragenter", "dragover"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzone.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzone.classList.remove("dragover");
    });
  });
  dropzone.addEventListener("drop", (event) => {
    const files = event.dataTransfer?.files;
    if (files?.length) onFiles(files);
  });
}

export {
  $,
  $$,
  escapeHtml,
  renderMarkdown,
  label,
  workModeLabel,
  skillLabel,
  renderJobTags,
  renderJobCard,
  renderJobDetail,
  buildJobContextText,
  formatDate,
  formatRelativeTime,
  formatSalary,
  fetchWithTimeout,
  setupDropzone,
  errorMessage,
  responseErrorMessage,
  showErrorDialog,
  showSuccessDialog,
};
