import {
  $,
  escapeHtml,
  renderMarkdown,
  fetchWithTimeout,
  workModeLabel,
} from "./helpers.js";
import { syncDialogScrollLock, validateAiConfig } from "./settings.js";
import { openJob, state } from "./jobs.js";
import { openWaterlooWorksJob } from "./waterlooworks.js";
import {
  toggleBookmarkJob,
  toggleWaterlooWorksBookmark,
  trackerState,
} from "./tracker.js";

// =========================================================
// SECTION 8: AI AGENT CHAT
// =========================================================

let currentSessionId = null;
let sessionList = [];
let attachedJobContext = null;
const renderedAgentJobs = new Map();
const attachSearchJobs = new Map();

const SESSION_STORAGE_KEY = "wecanfindintern_agent_session_v1";
const SIDEBAR_STORAGE_KEY = "wecanfindintern_agent_sidebar_collapsed_v1";

function persistSession() {
  try {
    localStorage.setItem(
      SESSION_STORAGE_KEY,
      JSON.stringify({ sessionId: currentSessionId, sessions: sessionList })
    );
  } catch (_) { }
}

function restoreSession() {
  try {
    const saved = JSON.parse(localStorage.getItem(SESSION_STORAGE_KEY) || "null");
    if (saved) {
      currentSessionId = saved.sessionId || null;
      sessionList = Array.isArray(saved.sessions) ? saved.sessions : [];
    }
  } catch (_) { }
}

function updateContextChip() {
  const preview = $("#agent-attachment-preview");
  const title = $("#agent-attached-job-title");
  if (!preview || !title) return;
  preview.hidden = !attachedJobContext;
  title.textContent = attachedJobContext
    ? `${attachedJobContext.title} @ ${attachedJobContext.company || "Company"}`
    : "";
}

function clearAttachedJob() {
  attachedJobContext = null;
  updateContextChip();
}

function attachActiveJobContext() {
  if (!state.activeJobContext) return false;
  attachedJobContext = {
    ...state.activeJobContext,
    source: state.activeJobContext.source || "public",
  };
  updateContextChip();
  return true;
}

function appendMessage(role, html) {
  const chat = $("#agent-chat");
  if (!chat) return;
  const el = document.createElement("div");
  el.className = `agent-message agent-${role}`;
  el.innerHTML = `<div class="agent-bubble">${html}</div>`;
  chat.appendChild(el);
  chat.scrollTop = chat.scrollHeight;
  return el;
}

function clearChat() {
  const chat = $("#agent-chat");
  if (chat) chat.innerHTML = "";
}

function sessionLabel(session) {
  const title = session.title || "Untitled";
  return title.length > 34 ? `${title.slice(0, 34)}…` : title;
}

async function renderSessionList() {
  try {
    const res = await fetch("/api/v1/agent/sessions");
    if (!res.ok) return;
    sessionList = await res.json();
    persistSession();
  } catch (_) {
    return;
  }
  const list = $("#agent-session-list");
  if (!list) return;
  list.innerHTML = sessionList
    .map((session) => {
      const active = session.id === currentSessionId ? " active" : "";
      return `<button type="button" class="agent-session-chip${active}" data-session-id="${escapeHtml(session.id)}" title="${escapeHtml(session.title || "Untitled conversation")}">${escapeHtml(sessionLabel(session))}</button>`;
    })
    .join("");
  list.querySelectorAll("[data-session-id]").forEach((button) => {
    button.addEventListener("click", () => {
      switchSession(button.dataset.sessionId);
    });
  });
}

async function switchSession(sessionId) {
  clearAttachedJob();
  currentSessionId = sessionId;
  persistSession();
  clearChat();
  await renderSessionList();
  loadMemoryStatus();
  try {
    const [messagesRes, toolsRes, approvalsRes] = await Promise.all([
      fetch(`/api/v1/agent/sessions/${sessionId}/messages`),
      fetch(`/api/v1/agent/sessions/${sessionId}/tool-calls`),
      fetch(`/api/v1/agent/sessions/${sessionId}/approvals`),
    ]);
    if (!messagesRes.ok) throw new Error("Could not load conversation");
    const messages = await messagesRes.json();
    const toolCalls = toolsRes.ok ? await toolsRes.json() : [];
    const approvals = approvalsRes.ok ? await approvalsRes.json() : [];
    const callsByMessage = new Map();
    toolCalls.forEach((call) => {
      if (!call.message_id) return;
      const calls = callsByMessage.get(call.message_id) || [];
      calls.push(call);
      callsByMessage.set(call.message_id, calls);
    });
    messages.forEach((message) => {
      const messageEl = appendMessage(
        message.role === "user" ? "user" : "assistant",
        message.role === "user"
          ? escapeText(message.content)
          : renderMarkdown(message.content)
      );
      if (message.role === "assistant") {
        renderRecommendationCards(callsByMessage.get(message.id) || [], messageEl);
      }
    });
    approvals.forEach(renderApprovalCard);
  } catch (err) {
    appendMessage("assistant", `<p class="md-p agent-error">⚠ ${escapeText(err.message)}</p>`);
  }
}

async function createNewSession() {
  clearAttachedJob();
  try {
    const res = await fetch("/api/v1/agent/sessions", { method: "POST" });
    if (!res.ok) throw new Error("Could not create a new conversation");
    const data = await res.json();
    currentSessionId = data.session.id;
    persistSession();
    clearChat();
    appendMessage(
      "assistant",
      "<p class=\"md-p\">New conversation started. What would you like to do?</p>"
    );
    await renderSessionList();
    loadMemoryStatus();
  } catch (err) {
    alert(err.message);
  }
}

function escapeText(text) {
  return escapeHtml(text).replace(/\n/g, "<br />");
}

function renderPreview(preview) {
  if (!preview) return "";
  const rows = [];
  if (preview.action === "add_interested" && Array.isArray(preview.jobs)) {
    rows.push("<h4>Add to Interested</h4>");
    preview.jobs.forEach((job) => {
      const already = job.already_tracked ? '<span class="tag">already tracked</span>' : "";
      rows.push(
        `<div class="agent-preview-row">${escapeHtml(job.source)} · ${escapeHtml(job.title || job.job_id)} · ${escapeHtml(job.company || "")} ${already}</div>`
      );
    });
  } else if (preview.action === "update_tracker_stage" && Array.isArray(preview.records)) {
    rows.push(`<h4>Change stage → ${escapeHtml(preview.stage)}</h4>`);
    preview.records.forEach((record) => {
      rows.push(
        `<div class="agent-preview-row">${escapeHtml(record.title)} @ ${escapeHtml(record.company)} — ${escapeHtml(record.current_stage)} → ${escapeHtml(record.new_stage)}</div>`
      );
    });
  } else if (preview.action === "remove_interested" && Array.isArray(preview.jobs)) {
    rows.push("<h4>Remove from Interested</h4>");
    preview.jobs.forEach((job) => {
      const protect = job.protected
        ? `<span class="tag">protected (${escapeHtml(job.tracked_stage)})</span>`
        : "";
      rows.push(
        `<div class="agent-preview-row">${escapeHtml(job.source)} · ${escapeHtml(job.title || job.job_id)} ${protect}</div>`
      );
    });
  } else if (preview.action === "update_profile" && Array.isArray(preview.changes)) {
    rows.push(`<h4>Profile changes (${preview.change_count})</h4>`);
    preview.changes.forEach((change) => {
      const oldValue = change.old == null ? "—" : String(change.old);
      const newValue = change.new == null ? "—" : String(change.new);
      rows.push(
        `<div class="agent-preview-row"><code>${escapeHtml(change.section)}.${escapeHtml(change.field)}</code>: ${escapeHtml(oldValue)} → ${escapeHtml(newValue)}</div>`
      );
    });
  } else {
    rows.push(
      `<pre class="agent-preview-json">${escapeHtml(JSON.stringify(preview, null, 2))}</pre>`
    );
  }
  return rows.join("");
}

function renderApprovalCard(approval) {
  const chat = $("#agent-chat");
  if (!chat) return;
  const card = document.createElement("div");
  card.className = "agent-approval-card";
  card.innerHTML = `
    <div class="agent-approval-title">Confirm action · ${escapeHtml(approval.tool_name)}</div>
    <div class="agent-approval-preview">${renderPreview(approval.preview)}</div>
    <div class="agent-approval-actions">
      <button type="button" class="primary-button" data-agent-approve>Confirm</button>
      <button type="button" class="secondary-button" data-agent-deny>Cancel</button>
    </div>`;
  chat.appendChild(card);
  card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  card.querySelector("[data-agent-approve]").addEventListener("click", () => {
    decideApproval(approval.id, true, card);
  });
  card.querySelector("[data-agent-deny]").addEventListener("click", () => {
    decideApproval(approval.id, false, card);
  });
}

const AGENT_BOOKMARK_ICON_SAVED = `<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>`;
const AGENT_BOOKMARK_ICON_OPEN = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>`;
const AGENT_VIEW_ICON = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>`;

function agentJobKey(job) {
  return `${job.source || "public"}:${job.job_id}`;
}

function isAgentJobTracked(job) {
  return job.source === "waterloo_work"
    ? trackerState.waterlooWorksTracked.has(String(job.job_id))
    : trackerState.trackedJobIds.has(String(job.job_id));
}

function renderRecommendationCards(toolCalls, afterElement = null) {
  const jobs = [];
  (toolCalls || []).forEach((call) => {
    if (call.status !== "succeeded") return;
    const data = call.result?.data;
    if (call.tool_name === "recommend_jobs") {
      jobs.push(...(data?.recommendations || []));
    } else if (call.tool_name === "search_jobs") {
      jobs.push(...(data?.waterloo_work || []), ...(data?.public || []));
    } else if (call.tool_name === "get_job_details" && data?.job_id) {
      jobs.push(data);
    }
  });
  const uniqueJobs = [...new Map(
    jobs.filter((job) => job?.job_id).map((job) => [agentJobKey(job), job])
  ).values()];
  if (!uniqueJobs.length) return;

  const wrapper = document.createElement("div");
  wrapper.className = "agent-recommendation-grid";
  wrapper.innerHTML = uniqueJobs.map((job) => {
    const key = agentJobKey(job);
    renderedAgentJobs.set(key, job);
    const sourceTag = job.source === "waterloo_work" ? "WaterlooWorks" : "Job Board";
    const tracked = isAgentJobTracked(job);
    const company = job.company || job.company_name || "Company not specified";
    const location = job.location_text || job.location || "";
    const meta = [company, location].filter(Boolean).join(" · ");
    const deadline = job.application_deadline
      ? `<span class="agent-job-deadline">Due ${escapeHtml(job.application_deadline)}</span>`
      : "";
    const score = job.match_score != null
      ? `<span class="agent-job-score">${escapeHtml(job.match_score)}% match</span>`
      : "";
    const skills = (job.matched_skills || []).slice(0, 4);
    const skillTags = skills.length
      ? `<div class="agent-job-skills">${skills.map((skill) => `<span class="tag">${escapeHtml(skill)}</span>`).join("")}</div>`
      : "";
    return `<article class="agent-recommendation-card" data-agent-job-key="${escapeHtml(key)}">
      <div class="agent-job-card-head">
        <div class="agent-job-card-tags">
          <span class="tag">${escapeHtml(sourceTag)}</span>
          ${score}
        </div>
        <div class="agent-job-card-tools">
          <button type="button" class="job-bookmark-btn${tracked ? " saved" : ""}" data-agent-save-job="${escapeHtml(key)}" title="${tracked ? "Tracked in Pipeline" : "Bookmark / Track Job"}">${tracked ? AGENT_BOOKMARK_ICON_SAVED : AGENT_BOOKMARK_ICON_OPEN}</button>
          <button type="button" class="job-view-btn" data-agent-view-job="${escapeHtml(key)}" title="View job details">${AGENT_VIEW_ICON}</button>
        </div>
      </div>
      <h4>${escapeHtml(job.title || "Untitled role")}</h4>
      <p class="agent-job-card-meta">${escapeHtml(meta)}</p>
      ${deadline}
      ${skillTags}
    </article>`;
  }).join("");
  if (afterElement) afterElement.insertAdjacentElement("afterend", wrapper);
  else $("#agent-chat").appendChild(wrapper);
  $("#agent-chat").scrollTop = $("#agent-chat").scrollHeight;
}

async function decideApproval(approvalId, approved, card) {
  card.querySelectorAll("button").forEach((button) => { button.disabled = true; });
  try {
    const res = await fetchWithTimeout(
      `/api/v1/agent/approvals/${approvalId}/decision`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved }),
      },
      60000,
    );
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Approval request failed");
    card.remove();
    appendMessage("assistant", renderMarkdown(data.message.content));
  } catch (err) {
    card.querySelectorAll("button").forEach((button) => { button.disabled = false; });
    appendMessage("assistant", `<p class="md-p agent-error">⚠ ${escapeText(err.message)}</p>`);
  }
}

async function ensureSession() {
  if (currentSessionId) return currentSessionId;
  const res = await fetch("/api/v1/agent/sessions", { method: "POST" });
  if (!res.ok) throw new Error("Could not create an agent session.");
  const data = await res.json();
  currentSessionId = data.session.id;
  persistSession();
  return currentSessionId;
}

function buildContext() {
  const ctx = attachedJobContext;
  if (!ctx) return null;
  return {
    job: {
      id: ctx.id,
      source: ctx.source || "public",
      title: ctx.title,
      company: ctx.company,
      jd: ctx.jd,
    },
  };
}

async function sendAgentMessage(text) {
  let config;
  try {
    config = validateAiConfig();
  } catch (err) {
    alert(err.message);
    return;
  }

  appendMessage("user", escapeText(text));
  $("#agent-input").value = "";

  const thinking = document.createElement("div");
  thinking.className = "agent-message agent-assistant";
  thinking.innerHTML = `<div class="agent-bubble agent-thinking">Thinking…</div>`;
  $("#agent-chat").appendChild(thinking);
  $("#agent-chat").scrollTop = $("#agent-chat").scrollHeight;

  try {
    const sessionId = await ensureSession();
    const res = await fetchWithTimeout(
      `/api/v1/agent/sessions/${sessionId}/messages`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: text,
          provider: config.provider,
          model_name: config.model_name,
          api_key: config.api_key,
          api_base: config.api_base || "",
          embedding_provider: config.embedding_provider,
          embedding_model: config.embedding_model,
          embedding_dimensions: config.embedding_dimensions,
          embedding_api_key: config.embedding_api_key,
          embedding_api_base: config.embedding_api_base || "",
          context: buildContext(),
        }),
      },
      180000,
    );
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Agent request failed");
    thinking.remove();
    const messageEl = appendMessage("assistant", renderMarkdown(data.message.content));
    renderRecommendationCards(data.tool_calls, messageEl);
    if (data.pending_approval) {
      renderApprovalCard(data.pending_approval);
    }
    clearAttachedJob();
    renderSessionList();
    loadMemoryStatus();
  } catch (err) {
    thinking.remove();
    appendMessage("assistant", `<p class="md-p agent-error">⚠ ${escapeText(err.message)}</p>`);
  }
}

function memoryTypeLabel(memoryType) {
  const labels = {
    USER_PREFERENCE: "Job preference",
    SKILL_PROFILE: "Skills",
    EDUCATION_PROFILE: "Education",
    CAREER_CONTEXT: "Career context",
    WORKFLOW_PREFERENCE: "Workflow",
  };
  return labels[memoryType] || String(memoryType || "Remembered detail").replaceAll("_", " ");
}

async function loadMemoryStatus() {
  const status = $("#agent-memory-status");
  const memoryList = $("#agent-memory-list");
  const memoryCount = $("#agent-memory-count");
  if (!status) return;
  if (!currentSessionId) {
    status.textContent = "Nothing saved yet.";
    if (memoryCount) memoryCount.textContent = "0";
    if (memoryList) memoryList.innerHTML = "";
    return;
  }
  try {
    const res = await fetch(`/api/v1/agent/sessions/${currentSessionId}/memory`);
    if (!res.ok) return;
    const data = await res.json();
    const memories = data.memories || [];
    if (memoryCount) memoryCount.textContent = String(memories.length);
    status.textContent = memories.length
      ? `${memories.length} saved ${memories.length === 1 ? "detail" : "details"}`
      : "Nothing saved yet.";
    if (memoryList) {
      memoryList.innerHTML = memories.length
        ? memories.map((memory) => `
            <div class="agent-memory-item">
              <div class="agent-memory-item-head">
                <span class="agent-memory-type">${escapeHtml(memoryTypeLabel(memory.memory_type))}</span>
                <button type="button" class="agent-memory-delete" data-memory-id="${escapeHtml(memory.id)}" title="Forget this detail" aria-label="Forget this detail">×</button>
              </div>
              <div class="agent-memory-text">${escapeHtml(memory.content)}</div>
            </div>`).join("")
        : "";
      memoryList.querySelectorAll("[data-memory-id]").forEach((button) => {
        button.addEventListener("click", () => deleteMemory(button.dataset.memoryId));
      });
    }
  } catch (_) { }
}

async function deleteMemory(memoryId) {
  try {
    const res = await fetch(`/api/v1/agent/memories/${memoryId}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Could not delete memory");
    await loadMemoryStatus();
  } catch (err) {
    alert(err.message);
  }
}

function viewAgentJob(key) {
  const job = renderedAgentJobs.get(key);
  if (!job) return;
  if (job.source === "waterloo_work") {
    openWaterlooWorksJob(String(job.job_id), (job.boards || []).join(","));
  } else {
    openJob(String(job.job_id));
  }
}

function updateAgentSaveButtons(key, job) {
  const tracked = isAgentJobTracked(job);
  document.querySelectorAll("[data-agent-save-job]").forEach((button) => {
    if (button.dataset.agentSaveJob === key) {
      button.classList.toggle("saved", tracked);
      button.innerHTML = tracked ? AGENT_BOOKMARK_ICON_SAVED : AGENT_BOOKMARK_ICON_OPEN;
      button.title = tracked ? "Tracked in Pipeline" : "Bookmark / Track Job";
    }
  });
}

async function saveAgentJob(key, button) {
  const job = renderedAgentJobs.get(key);
  if (!job) return;
  button.disabled = true;
  try {
    if (job.source === "waterloo_work") {
      await toggleWaterlooWorksBookmark(String(job.job_id));
    } else {
      await toggleBookmarkJob(String(job.job_id));
    }
    updateAgentSaveButtons(key, job);
  } finally {
    button.disabled = false;
  }
}

function closeAttachDialog() {
  const dialog = $("#agent-attach-dialog");
  if (dialog?.open) dialog.close();
  syncDialogScrollLock();
}

function renderCurrentJobOption() {
  const option = $("#agent-current-job-option");
  if (!option) return;
  const job = state.activeJobContext;
  option.hidden = !job;
  option.innerHTML = job
    ? `<p>Currently viewed job</p>
       <div class="agent-attach-result">
         <div class="agent-attach-result-copy">
           <strong>${escapeHtml(job.title || "Untitled role")}</strong>
           <span>${escapeHtml(job.company || "Company not specified")}</span>
         </div>
         <button type="button" class="primary-button compact-button" data-attach-current-job>Attach</button>
       </div>`
    : "";
}

function renderAttachSearchResults(jobs, message = "") {
  const results = $("#agent-attach-results");
  if (!results) return;
  if (!jobs.length) {
    results.innerHTML = `<p class="muted-copy">${escapeHtml(message || "No matching jobs found.")}</p>`;
    return;
  }
  results.innerHTML = jobs.map((job) => {
    const key = agentJobKey(job);
    attachSearchJobs.set(key, job);
    const source = job.source === "waterloo_work" ? "WaterlooWorks" : "Job Board";
    const meta = [job.company, job.location].filter(Boolean).join(" · ");
    return `<div class="agent-attach-result">
      <div class="agent-attach-result-copy">
        <strong>${escapeHtml(job.title || "Untitled role")}</strong>
        <span><span class="tag">${escapeHtml(source)}</span>${escapeHtml(meta || "Company not specified")}</span>
      </div>
      <button type="button" class="secondary-button compact-button" data-attach-job-key="${escapeHtml(key)}">Attach</button>
    </div>`;
  }).join("");
}

async function fetchAttachJobList(url, source) {
  const response = await fetch(url);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || `Could not search ${source}.`);
  if (source === "waterloo_work") {
    return (data.items || []).map((job) => ({
      source,
      job_id: String(job.source_job_id),
      title: job.title,
      company: job.organization,
      location: job.location_text,
      boards: job.boards || [],
    }));
  }
  return (data.items || []).map((job) => ({
    source,
    job_id: String(job.id),
    title: job.title,
    company: job.company_name,
    location: job.location?.display_name,
  }));
}

async function searchAttachJobs() {
  const query = $("#agent-attach-query")?.value.trim() || "";
  const button = $("#agent-attach-search-button");
  attachSearchJobs.clear();
  if (button) {
    button.disabled = true;
    button.textContent = "Searching…";
  }
  renderAttachSearchResults([], "Searching Job Board and WaterlooWorks…");
  const publicParams = new URLSearchParams({ limit: "8" });
  const waterlooParams = new URLSearchParams({ limit: "8" });
  if (query) {
    publicParams.set("query", query);
    waterlooParams.set("query", query);
  }
  try {
    const searches = await Promise.allSettled([
      fetchAttachJobList(`/api/v1/jobs?${publicParams}`, "public"),
      fetchAttachJobList(`/api/v1/waterlooworks/jobs?${waterlooParams}`, "waterloo_work"),
    ]);
    const jobs = searches.flatMap((result) => result.status === "fulfilled" ? result.value : []);
    const failedCount = searches.filter((result) => result.status === "rejected").length;
    const emptyMessage = failedCount === searches.length
      ? "Job search is temporarily unavailable. Please try again."
      : "No matching jobs found.";
    renderAttachSearchResults(jobs, emptyMessage);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "Search";
    }
  }
}

function publicJobContext(job) {
  return {
    id: String(job.id),
    source: "public",
    title: job.title,
    company: job.company_name,
    jd: `${job.title || "Role"} at ${job.company_name || "Company"}\n\nLocation: ${job.location?.display_name || "Unspecified"}\nWork Mode: ${workModeLabel(job.work_mode)}\nRecruiting Term: ${job.recruiting_term?.display_name || "Unspecified"}\n\nDescription:\n${job.description || ""}`,
  };
}

function waterlooWorksJobContext(job) {
  return {
    id: String(job.source_job_id),
    source: "waterloo_work",
    title: job.title,
    company: job.organization,
    jd: `${job.title || "Role"} at ${job.organization || "Company"}\n\nJob ID: ${job.source_job_id}\nLocation: ${job.location_text || "Unspecified"}\nWork Mode: ${workModeLabel(job.work_mode)}\nApplication Deadline: ${job.application_deadline || "Unspecified"}\n\nDescription:\n${job.description || ""}`,
  };
}

async function attachSelectedJob(key, button) {
  const summary = attachSearchJobs.get(key);
  if (!summary) return;
  button.disabled = true;
  button.textContent = "Adding…";
  try {
    const endpoint = summary.source === "waterloo_work"
      ? `/api/v1/waterlooworks/jobs/${encodeURIComponent(summary.job_id)}`
      : `/api/v1/jobs/${encodeURIComponent(summary.job_id)}`;
    const response = await fetch(endpoint);
    const job = await response.json();
    if (!response.ok) throw new Error(job.detail || "Could not attach this job.");
    attachedJobContext = summary.source === "waterloo_work"
      ? waterlooWorksJobContext(job)
      : publicJobContext(job);
    updateContextChip();
    closeAttachDialog();
    $("#agent-input")?.focus();
  } catch (error) {
    alert(error.message);
    button.disabled = false;
    button.textContent = "Attach";
  }
}

function openAttachDialog() {
  const dialog = $("#agent-attach-dialog");
  if (!dialog) return;
  renderCurrentJobOption();
  $("#agent-attach-query").value = "";
  renderAttachSearchResults([], "Loading recent jobs…");
  dialog.showModal();
  syncDialogScrollLock();
  searchAttachJobs();
  $("#agent-attach-query")?.focus();
}

function resizeAgentInput() {
  const input = $("#agent-input");
  if (!input) return;
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 150)}px`;
}

function setSidebarCollapsed(collapsed, { persist = true } = {}) {
  const layout = $("#agent-layout");
  const toggle = $("#agent-sidebar-toggle");
  if (!layout || !toggle) return;
  layout.classList.toggle("sidebar-collapsed", collapsed);
  toggle.setAttribute("aria-expanded", String(!collapsed));
  toggle.setAttribute("aria-label", collapsed ? "Expand conversation sidebar" : "Collapse conversation sidebar");
  toggle.title = collapsed ? "Expand sidebar" : "Collapse sidebar";
  if (persist) {
    try { localStorage.setItem(SIDEBAR_STORAGE_KEY, String(collapsed)); } catch (_) { }
  }
}

$("#agent-form")?.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = $("#agent-input").value.trim();
  if (!text) return;
  sendAgentMessage(text);
  resizeAgentInput();
});

$("#agent-new-session")?.addEventListener("click", createNewSession);
$("#agent-attach-job")?.addEventListener("click", openAttachDialog);
$("#agent-remove-attachment")?.addEventListener("click", clearAttachedJob);
$("#close-agent-attach-dialog")?.addEventListener("click", closeAttachDialog);
$("#cancel-agent-attach-dialog")?.addEventListener("click", closeAttachDialog);
$("#agent-attach-dialog")?.addEventListener("click", (event) => {
  if (event.target === $("#agent-attach-dialog")) closeAttachDialog();
});
$("#agent-attach-search-button")?.addEventListener("click", searchAttachJobs);
$("#agent-attach-query")?.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    searchAttachJobs();
  }
});
$("#agent-current-job-option")?.addEventListener("click", (event) => {
  if (!event.target.closest("[data-attach-current-job]")) return;
  if (attachActiveJobContext()) {
    closeAttachDialog();
    $("#agent-input")?.focus();
  }
});
$("#agent-attach-results")?.addEventListener("click", (event) => {
  const button = event.target.closest("[data-attach-job-key]");
  if (button) attachSelectedJob(button.dataset.attachJobKey, button);
});
$("#agent-sidebar-toggle")?.addEventListener("click", () => {
  setSidebarCollapsed(!$("#agent-layout")?.classList.contains("sidebar-collapsed"));
});
$("#agent-input")?.addEventListener("input", resizeAgentInput);
$("#agent-input")?.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("#agent-form")?.requestSubmit();
  }
});
$("#agent-chat")?.addEventListener("click", (event) => {
  const suggestion = event.target.closest("[data-agent-prompt]");
  const viewButton = event.target.closest("[data-agent-view-job]");
  const saveButton = event.target.closest("[data-agent-save-job]");
  if (suggestion) {
    $("#agent-input").value = suggestion.dataset.agentPrompt;
    resizeAgentInput();
    $("#agent-input").focus();
  } else if (viewButton) {
    viewAgentJob(viewButton.dataset.agentViewJob);
  } else if (saveButton) {
    saveAgentJob(saveButton.dataset.agentSaveJob, saveButton);
  }
});

restoreSession();
try {
  const savedCollapsed = localStorage.getItem(SIDEBAR_STORAGE_KEY) === "true";
  setSidebarCollapsed(
    savedCollapsed || window.matchMedia("(max-width: 640px)").matches,
    { persist: false },
  );
} catch (_) { }
renderSessionList();
if (currentSessionId) {
  switchSession(currentSessionId);
} else {
  loadMemoryStatus();
}

export { attachActiveJobContext, updateContextChip, renderSessionList };
