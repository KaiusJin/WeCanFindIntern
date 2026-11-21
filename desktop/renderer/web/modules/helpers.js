import { showToast } from "./feedback.js";
export { showToast, showFieldError, clearFieldError, confirmDecision, showSystemFault, announceStatus, createActionFeedback } from "./feedback.js";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => document.querySelectorAll(selector);
// Update both the native value and the library's React input state on prefills and resets.
function fillGlassInput(input, value) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}
// Résumés often omit the scheme when displaying a profile or portfolio link.
function resumeContactUrl(rawValue) {
  const value = String(rawValue ?? "").trim();
  if (!value || /\s|\\/.test(value)) return "";
  const hasScheme = /^[a-z][a-z0-9+.-]*:/i.test(value);
  if (hasScheme && !/^https?:\/\/[^/]/i.test(value)) return "";
  if (!hasScheme && /^[./?#:]/.test(value)) return "";
  try {
    const url = new URL(hasScheme ? value : "https://" + value);
    if (url.username || url.password || (!hasScheme && !url.hostname.includes("."))) return "";
    if (url.hostname.startsWith(".") || url.hostname.includes("..")) return "";
    return url.href;
  } catch {
    return "";
  }
}

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

function showErrorToast(error, { title } = {}) {
  showToast(errorMessage(error), { title, variant: "error" });
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

function renderJobBookmark(id, waterlooWorks, saved) {
  const buttonClass = waterlooWorks ? "ww-bookmark-btn" : "job-bookmark-btn";
  const idAttribute = waterlooWorks ? "data-source-job-id" : "data-job-id";
  return `<button type="button" class="${buttonClass} control-glass${saved ? " saved" : ""}"
    ${idAttribute}="${escapeHtml(id)}" aria-pressed="${saved}"
    title="${saved ? "Saved in Applications" : "Save to Interested"}"
    aria-label="${saved ? "Saved in Applications" : "Save to Interested"}">
    <svg class="icon" aria-hidden="true"><use href="/ui/icons.svg#bookmark"></use></svg>
  </button>`;
}

function postingFields(job, waterlooWorks) {
  if (waterlooWorks) {
    // Submitted applications is a filter, not a posting board.
    const boards = (job.boards || []).filter(board => board !== "applications");
    return {
      id: job.source_job_id,
      company: job.organization,
      location: job.location_text,
      board: boards.map(board => job.board_labels?.[board] || label(board)).join(" · "),
      deadline: job.submitted_application_deadline || job.application_deadline,
    };
  }
  return {
    id: job.id,
    company: job.company_name,
    location: job.location?.display_name,
    term: job.recruiting_term?.display_name,
  };
}

function renderJobCard(job, { waterlooWorks = false, isSaved = false, selected = false } = {}) {
  const { id, company, location, board, deadline, term } = postingFields(job, waterlooWorks);
  const locationNote = waterlooWorks ? workModeLabel(job.work_mode) : term;
  const salary = formatSalary(job.salary);
  const date = job.date_posted || job.published_at;
  const paneId = waterlooWorks ? "ww-job-detail-pane" : "public-job-detail-pane";
  return `<article class="job-card posting-row${selected ? " selected" : ""}" data-id="${escapeHtml(id)}">
    <div class="posting-role">
      <h3><button class="job-title-button" type="button" aria-expanded="${selected}" aria-controls="${paneId}">${escapeHtml(job.title)}</button></h3>
      <p>${escapeHtml(company || "Company not specified")}</p>
      ${waterlooWorks ? `<span class="posting-board">${board ? "Board: " + escapeHtml(board) : "Board not specified"}</span>` : ""}
    </div>
    <div class="posting-place">
      <span>${escapeHtml(location || "Location not specified")}</span>
      ${locationNote ? `<span>${escapeHtml(locationNote)}</span>` : ""}
      ${waterlooWorks ? `<span>Job ID ${escapeHtml(id)}</span>` : ""}
    </div>
    <div class="posting-pay">
      ${waterlooWorks || salary !== "Salary not disclosed" ? `<span>${escapeHtml(salary)}</span>` : ""}
      ${waterlooWorks ? `<span class="posting-deadline">${deadline ? "Due " + escapeHtml(deadline) : "Deadline not specified"}</span>`
        : date ? `<time>${escapeHtml(formatDate(date))}</time>` : ""}
    </div>
    ${renderJobBookmark(id, waterlooWorks, isSaved)}
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

function postingLinks(links) {
  const sources = [...new Set(links.filter(Boolean))].flatMap((link) => {
    try {
      const url = new URL(link);
      return ["http:", "https:", "mailto:"].includes(url.protocol) ? [url] : [];
    } catch { return []; }
  });
  if (!sources.length) return "";
  if (sources.length === 1) {
    return `<a class="primary-button" href="${escapeHtml(sources[0].href)}" target="_blank" rel="noreferrer">Apply ↗</a>`;
  }
  const options = sources.map((url) =>
    `<a href="${escapeHtml(url.href)}" target="_blank" rel="noreferrer" title="${escapeHtml(url.href)}">${escapeHtml(url.hostname.replace(/^www\./, "") || url.pathname)} ↗</a>`
  ).join("");
  return `<posting-menu class="posting-apply-menu"><details class="job-tools-menu posting-source-menu"><summary class="primary-button">Apply</summary><div class="glass">${options}</div></details></posting-menu>`;
}

function renderJobDetail(job, { waterlooWorks = false, isSaved = false } = {}) {
  const { id, company, location, board, deadline, term } = postingFields(job, waterlooWorks);
  const eyebrow = [job.opportunity_type, job.job_category]
    .filter(value => value && value !== "unknown").map(label).join(" · ");
  const meta = waterlooWorks ? `Job ID ${id}` : job.date_posted && `Posted ${formatDate(job.date_posted)}`;
  const facts = [
    { label: "Location", value: location },
    { label: "Work mode", value: workModeLabel(job.work_mode) },
    { label: "Salary", value: formatSalary(job.salary) },
  ];
  if (waterlooWorks) {
    facts.push({ label: "Application deadline", value: deadline }, { label: "Source board", value: board });
    if (job.application_status) facts.push({ label: "Application status", value: job.application_status });
  } else {
    facts.push({ label: "Recruiting term", value: term });
  }
  const links = waterlooWorks ? [job.application_url || job.source_url]
    : (job.sources || []).map(source => source.direct_url || source.url);
  const skills = job.source_skills?.length ? job.source_skills : job.skill_tags;
  const skillsText = (skills || [])
    .slice(0, 15)
    .map(skillLabel)
    .filter(Boolean)
    .join(", ");
  const aiActionButtons = `
    <details class="job-tools-menu"><summary>Career tools</summary>
      <div class="glass">
        <button class="btn-ai-action" type="button" data-ai-target="tab-ats-match">Job Match</button>
        <button class="btn-ai-action" type="button" data-ai-target="tab-interview">Mock Interview</button>
        <button class="btn-ai-action" type="button" data-ai-target="tab-cover-letter">Cover Letter</button>
        <button class="btn-ai-action" type="button" data-ai-target="tab-agent">Ask AI Assistant</button>
      </div>
    </details>`;
  const descriptionHtml = job.description ? renderMarkdown(job.description) : "<p>No detailed description is available for this job.</p>";
  return `<article class="posting-document">
      <header class="posting-masthead">
        <div class="posting-employer">
          <strong>${escapeHtml(company || "Company not specified")}</strong>${eyebrow ? `<span>${escapeHtml(eyebrow)}</span>` : ""}
        </div>
        <h2>${escapeHtml(job.title)}</h2>
        ${meta ? `<p class="posting-date">${escapeHtml(meta)}</p>` : ""}
      </header>
      <div class="job-ai-actions posting-actions"><span class="posting-actions-material glass" aria-hidden="true"></span>${postingLinks(links)}<posting-menu>${aiActionButtons}</posting-menu>${renderJobBookmark(id, waterlooWorks, isSaved)}</div>
      <dl class="posting-facts">${facts.map((fact) => `<div><dt>${escapeHtml(fact.label)}</dt><dd>${escapeHtml(fact.value || "Not specified")}</dd></div>`).join("")}</dl>
      <section class="posting-body"><h3>Job description</h3><div class="detail-description">${descriptionHtml}</div></section>
      ${skillsText ? `<section class="posting-skills"><h3>Skills</h3><p>${escapeHtml(skillsText)}</p></section>` : ""}
    </article>`;
}

function formatDate(value) {
  if (!value) return "Date not specified";
  // Date-only posting fields describe a calendar day, independent of timezone.
  const date = new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? value + "T12:00:00" : value);
  if (Number.isNaN(date.getTime())) return "Date not specified";
  return new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "short", day: "numeric" }).format(date);
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
  fillGlassInput,
  resumeContactUrl,
  escapeHtml,
  renderMarkdown,
  label,
  workModeLabel,
  skillLabel,
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
  showErrorToast,
};
