import {
  $,
  escapeHtml,
  renderMarkdown,
  fetchWithTimeout,
  errorMessage,
  showToast,
  announceStatus,
  showFieldError,
  clearFieldError,
  confirmDecision,
} from "./helpers.js?v=20260901-error-dialog-minimal-v1";
import { readSseEvents } from "./sse.js";
import { getAiConfigOrShowError } from "./settings.js?v=20260902-shared-components-v1";
import {
  jobContextState,
  publicJobContext,
  waterlooWorksJobContext,
} from "./job-context.js?v=20260831-jobboard-parity-v3";
import {
  bookmarkState,
  loadPublicBookmarks,
  loadWaterlooWorksBookmarks,
  toggleBookmarkJob,
  toggleWaterlooWorksBookmark,
} from "./bookmarks.js";

// =========================================================
// SECTION 8: AI AGENT CHAT
// =========================================================

let activeStream = null;
let currentSessionId = null;
let sessionList = [];
let attachedJobContexts = [];
let agentJobDetailTrigger = null;
const renderedAgentJobs = new Map();
const attachSearchJobs = new Map();

const SESSION_STORAGE_KEY = "wecanfindintern_agent_session_v1";
const MAX_ATTACHED_JOBS = 5;
const ATTACH_SEARCH_LIMIT = 50;
const AGENT_TRASH_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M3.5 7h17"></path><path d="M8.5 7V5.25c0-.69.56-1.25 1.25-1.25h4.5c.69 0 1.25.56 1.25 1.25V7"></path><path d="M6 7l1 13h10l1-13"></path><path d="M10 11v5M14 11v5"></path></svg>`;

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

function updateComposer() {
  $("#agent-form").dispatchEvent(new CustomEvent("assistant:composer", {
    detail: { attached: attachedJobContexts.length, streaming: Boolean(activeStream) },
  }));
}

function clearAttachedJobs() {
  attachedJobContexts = [];
  updateComposer();
}

function attachedContextKey(job) {
  return `${job.source || "public"}:${job.id}`;
}

function isContextAttached(job) {
  const key = attachedContextKey(job);
  return attachedJobContexts.some((item) => attachedContextKey(item) === key);
}

function addAttachedJobContext(job) {
  if (!job || !job.id || isContextAttached(job)) return true;
  if (attachedJobContexts.length >= MAX_ATTACHED_JOBS) {
    showToast(`You can attach up to ${MAX_ATTACHED_JOBS} jobs at once.`, { variant: "info" });
    return false;
  }
  attachedJobContexts.push({ ...job, source: job.source || "public" });
  updateComposer();
  return true;
}

function attachActiveJobContext() {
  if (!jobContextState.activeJobContext) return false;
  return addAttachedJobContext(jobContextState.activeJobContext);
}

function removeAttachedJob(key) {
  attachedJobContexts = attachedJobContexts.filter((job) => attachedContextKey(job) !== key);
  updateComposer();
  renderCurrentJobOption();
  renderAttachSearchResults([...attachSearchJobs.values()]);
  focusAttachmentAction(key);
}

function focusAttachmentAction(key) {
  if ($("#agent-attach-dialog").hidden) return;
  const escaped = CSS.escape(key);
  $("#agent-attach-results").querySelector(`[data-attach-job-key="${escaped}"], [data-remove-attached-job="${escaped}"]`)?.focus();
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

function renderAgentWelcome() {
  const template = document.querySelector("#agent-welcome-template");
  $("#agent-chat").replaceChildren(template.content.cloneNode(true));
}

async function renderSessionList() {
  try {
    const res = await fetch("/api/v1/agent/sessions");
    if (!res.ok) throw new Error("History unavailable");
    sessionList = await res.json();
    persistSession();
  } catch (_) {
    if (!sessionList.length) {
      $("#agent-session-list").innerHTML = '<p class="conversation-list-status" role="status">Could not load conversations.</p>';
      return;
    }
  }
  const activeSession = sessionList.find((session) => session.id === currentSessionId);
  $("#agent-session-title").textContent = activeSession?.title || "New conversation";
  const list = $("#agent-session-list");
  if (!list) return;
  list.innerHTML = sessionList
    .map((session) => {
      const active = session.id === currentSessionId ? " active" : "";
      const title = session.title || "Untitled conversation";
      return `<div class="agent-session-chip-row">
        <button type="button" class="agent-session-chip${active}" data-session-id="${escapeHtml(session.id)}" aria-current="${Boolean(active)}" title="${escapeHtml(title)}">${escapeHtml(title)}</button>
        <button type="button" class="agent-session-delete glass" data-delete-session-id="${escapeHtml(session.id)}" aria-label="Delete conversation: ${escapeHtml(title)}" title="Delete conversation">${AGENT_TRASH_ICON}</button>
      </div>`;
    })
    .join("") || '<p class="conversation-list-status">No previous conversations.</p>';
}

async function deleteConversation(sessionId, button) {
  const session = sessionList.find((item) => item.id === sessionId);
  if (!session || button.disabled) return;
  const confirmed = await confirmDecision({
    title: "Delete conversation?",
    message: `“${session.title || "Untitled conversation"}” and all its messages will be permanently deleted.`,
    confirmLabel: "Delete conversation",
    destructive: true,
  });
  if (!confirmed || button.disabled) return;
  button.disabled = true;
  try {
    const res = await fetch(`/api/v1/agent/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Could not delete conversation");

    const wasCurrent = sessionId === currentSessionId;
    sessionList = sessionList.filter((session) => session.id !== sessionId);
    announceStatus("Conversation deleted.");
    if (!wasCurrent) {
      persistSession();
      await renderSessionList();
      return;
    }

    activeStream?.abort();
    const nextSessionId = sessionList[0]?.id || null;
    currentSessionId = nextSessionId;
    persistSession();
    clearAttachedJobs();
    clearChat();
    await renderSessionList();
    if (nextSessionId) {
      await switchSession(nextSessionId);
    } else {
      renderAgentWelcome();
      loadMemoryStatus();
    }
  } catch (err) {
    showToast(errorMessage(err, "Could not delete conversation."), { variant: "error" });
  } finally {
    button.disabled = false;
    if (!button.isConnected && !$("#tab-agent").hidden) $("#agent-history summary").focus();
  }
}

async function switchSession(sessionId) {
  clearAttachedJobs();
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
    if (!messages.length && !approvals.length) renderAgentWelcome();
  } catch (err) {
    showToast(errorMessage(err, "Conversation could not be loaded."), { variant: "error" });
  }
}

async function createNewSession() {
  clearAttachedJobs();
  try {
    const res = await fetch("/api/v1/agent/sessions", { method: "POST" });
    if (!res.ok) throw new Error("Could not create a new conversation");
    const data = await res.json();
    currentSessionId = data.session.id;
    persistSession();
    renderAgentWelcome();
    $("#agent-input").focus();
    await renderSessionList();
    loadMemoryStatus();
  } catch (err) {
    showToast(errorMessage(err, "Conversation could not be created."), { variant: "error" });
  }
}

function escapeText(text) {
  return escapeHtml(text).replace(/\n/g, "<br />");
}

function renderPreview(preview) {
  if (!preview) return "";
  const rows = [];
  if (preview.action === "add_into_tracker" && Array.isArray(preview.jobs)) {
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
  } else if (
    (preview.action === "remove_from_tracker" || preview.action === "remove_interested") &&
    (Array.isArray(preview.records) || Array.isArray(preview.jobs))
  ) {
    rows.push("<h4>Remove from Tracker</h4>");
    (preview.records || preview.jobs).forEach((record) => {
      const status = record.status === "not_found"
        ? '<span class="tag">not found</span>'
        : record.stage
          ? `<span class="tag">${escapeHtml(record.stage)}</span>`
          : "";
      rows.push(
        `<div class="agent-preview-row">${escapeHtml(record.source || "tracker")} · ${escapeHtml(record.title || record.job_id || record.application_id)} ${status}</div>`
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
  const existing = [...chat.querySelectorAll("[data-agent-approval-id]")]
    .find((item) => item.dataset.agentApprovalId === approval.id);
  if (existing) return;
  const card = document.createElement("div");
  card.className = "agent-approval-card";
  card.dataset.agentApprovalId = approval.id;
  card.innerHTML = `
    <div class="agent-approval-title">Confirm action · ${escapeHtml(approval.tool_name)}</div>
    <div class="agent-approval-preview">${renderPreview(approval.preview)}</div>
    <div class="agent-approval-actions">
      <button type="button" class="glass" data-agent-approve>Review action</button>
      <button type="button" class="glass" data-agent-deny>Decline</button>
    </div>`;
  chat.appendChild(card);
  card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  card.querySelector("[data-agent-approve]").addEventListener("click", async () => {
    const accepted = await confirmDecision({
      title: "Apply this assistant action?",
      message: card.querySelector(".agent-approval-preview").innerText || approval.tool_name.replaceAll("_", " "),
      confirmLabel: "Apply action",
      destructive: ["remove_from_tracker", "remove_interested"].includes(approval.tool_name),
    });
    if (accepted && card.isConnected) decideApproval(approval.id, true, card);
  });
  card.querySelector("[data-agent-deny]").addEventListener("click", () => {
    decideApproval(approval.id, false, card);
  });
}

const TRACKER_WRITE_TOOLS = new Set([
  "add_into_tracker",
  "update_tracker_stage",
  "remove_from_tracker",
  "remove_interested",
]);

function removeApprovalCard(approvalId) {
  if (!approvalId) return;
  [...document.querySelectorAll("[data-agent-approval-id]")]
    .find((item) => item.dataset.agentApprovalId === approvalId)
    ?.remove();
}

async function applyAgentTurnEffects(result) {
  const successfulTrackerWrite = (result?.tool_calls || []).some(
    (call) => call.status === "succeeded" && TRACKER_WRITE_TOOLS.has(call.tool_name),
  );
  if (!successfulTrackerWrite) return;
  await refreshAgentBookmarkState();
  document.dispatchEvent(new CustomEvent("tracker:data-invalidated"));
}

async function finalizeAgentTurn(result, bubble = null) {
  const content = result?.message?.content;
  if (content) {
    if (!bubble) bubble = appendMessage("assistant", renderMarkdown(content));
    else bubble.querySelector(".agent-bubble").innerHTML = renderMarkdown(content);
  }
  removeApprovalCard(result?.approval?.id);
  await applyAgentTurnEffects(result);
  return bubble;
}

const AGENT_BOOKMARK_ICON_SAVED = `<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>`;
const AGENT_BOOKMARK_ICON_OPEN = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>`;
const AGENT_VIEW_ICON = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>`;

function agentJobKey(job) {
  return `${job.source || "public"}:${job.job_id}`;
}

function isAgentJobTracked(job) {
  return job.source === "waterloo_work"
    ? bookmarkState.waterlooWorksJobs.has(String(job.job_id))
    : bookmarkState.publicJobs.has(String(job.job_id));
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
    }
  });
  const jobsByKey = new Map();
  jobs.filter((job) => job?.job_id).forEach((job) => {
    const key = agentJobKey(job);
    jobsByKey.set(key, { ...(jobsByKey.get(key) || {}), ...job });
  });
  const uniqueJobs = [...jobsByKey.values()];
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
    const matchScore = job.match_score ?? job.fit_score;
    const score = matchScore != null
      ? `<span class="agent-job-score">${escapeHtml(matchScore)}% match</span>`
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
          <button type="button" class="glass agent-icon-btn${tracked ? " saved" : ""}" data-agent-save-job="${escapeHtml(key)}" title="${tracked ? "Tracked in Pipeline" : "Bookmark / Track Job"}">${tracked ? AGENT_BOOKMARK_ICON_SAVED : AGENT_BOOKMARK_ICON_OPEN}</button>
          <button type="button" class="glass job-view-btn" data-agent-view-job="${escapeHtml(key)}" title="View job details">${AGENT_VIEW_ICON}</button>
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
    await finalizeAgentTurn(data);
  } catch (err) {
    card.querySelectorAll("button").forEach((button) => { button.disabled = false; });
    showToast(errorMessage(err, "Agent action failed."), { variant: "error" });
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
  if (!attachedJobContexts.length) return null;
  return {
    jobs: attachedJobContexts.map((ctx) => ({
      id: ctx.id,
      source: ctx.source || "public",
      title: ctx.title,
      company: ctx.company,
      location: ctx.location,
      work_mode: ctx.workMode,
      application_deadline: ctx.applicationDeadline,
      jd: ctx.jd,
    })),
  };
}

async function sendAgentMessage(text) {
  const config = getAiConfigOrShowError();
  if (!config) return;

  $("#agent-chat .agent-welcome")?.remove();
  appendMessage("user", escapeText(text));
  $("#agent-input").value = "";

  const approvalButtons = [
    ...document.querySelectorAll("[data-agent-approval-id] button"),
  ];
  approvalButtons.forEach((button) => { button.disabled = true; });

  const streamAbort = new AbortController();
  activeStream = streamAbort;
  updateComposer();

  const thinking = document.createElement("div");
  thinking.className = "agent-message agent-assistant";
  thinking.innerHTML = `<div class="agent-bubble agent-thinking">Thinking…</div>`;
  $("#agent-chat").appendChild(thinking);
  $("#agent-chat").scrollTop = $("#agent-chat").scrollHeight;

  let bubble = null;
  let accumulated = "";
  let receivedDone = false;
  const showDelta = (delta) => {
    if (!bubble) {
      thinking.remove();
      bubble = appendMessage("assistant", "");
    }
    accumulated += delta;
    bubble.querySelector(".agent-bubble").innerHTML = renderMarkdown(accumulated);
    $("#agent-chat").scrollTop = $("#agent-chat").scrollHeight;
  };

  try {
    const sessionId = await ensureSession();
    const res = await fetchWithTimeout(
      `/api/v1/agent/sessions/${sessionId}/messages/stream`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: streamAbort.signal,
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
      300000,
    );
    if (!res.ok || !res.body) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || "Agent request failed");
    }

    for await (const event of readSseEvents(res)) {
        if (event.type === "text_delta") {
          showDelta(event.delta);
        } else if (event.type === "tool") {
          thinking.remove();
          renderRecommendationCards([event.tool_call]);
        } else if (event.type === "approval") {
          thinking.remove();
          renderApprovalCard(event.approval);
        } else if (event.type === "error") {
          throw new Error(event.detail || "Agent request failed");
        } else if (event.type === "done") {
          thinking.remove();
          receivedDone = true;
          currentSessionId = event.result.session.id;
          persistSession();
          updateComposer();
          renderSessionList();
          loadMemoryStatus();
          bubble = await finalizeAgentTurn(event.result, bubble);
        }
    }
    if (!receivedDone) {
      throw new Error("The Agent response ended before a final result was received.");
    }
  } catch (err) {
    if (thinking.parentElement) thinking.remove();
    if (streamAbort.signal.aborted) {
      // User pressed Stop: keep the partial reply, mark it, no error UI.
      if (bubble) {
        bubble.querySelector(".agent-bubble").insertAdjacentHTML(
          "beforeend",
          '<p class="md-p agent-stopped">⏹ Stopped.</p>',
        );
      }
    } else {
      showToast(errorMessage(err, "Agent response failed."), { variant: "error" });
    }
  } finally {
    approvalButtons.forEach((button) => {
      if (button.isConnected) button.disabled = false;
    });
    activeStream = null;
    updateComposer();
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

let memoryRequestSequence = 0;

async function loadMemoryStatus() {
  const request = ++memoryRequestSequence;
  const status = $("#agent-memory-dialog-status");
  const memoryList = $("#agent-memory-dialog-list");
  const dialogMemoryCount = $("#agent-memory-dialog-count");
  if (!status) return;
  if (!currentSessionId) {
    status.textContent = "Nothing saved yet.";
    if (dialogMemoryCount) dialogMemoryCount.textContent = "0";
    if (memoryList) memoryList.innerHTML = "";
    return;
  }
  try {
    const res = await fetch(`/api/v1/agent/sessions/${currentSessionId}/memory`);
    if (!res.ok) throw new Error("Could not load saved memories.");
    const data = await res.json();
    if (request !== memoryRequestSequence) return;
    const memories = data.memories || [];
    if (dialogMemoryCount) dialogMemoryCount.textContent = String(memories.length);
    status.textContent = memories.length
      ? `${memories.length} saved ${memories.length === 1 ? "detail" : "details"}`
      : "Nothing saved yet.";
    if (memoryList) {
      memoryList.innerHTML = memories.length
        ? memories.map((memory) => `
            <div class="agent-memory-item">
              <div class="agent-memory-item-head">
                <span class="agent-memory-type">${escapeHtml(memoryTypeLabel(memory.memory_type))}</span>
                <button type="button" class="agent-memory-delete glass" data-memory-id="${escapeHtml(memory.id)}" title="Forget this detail" aria-label="Forget ${escapeHtml(memory.content)}">×</button>
              </div>
              <div class="agent-memory-text">${escapeHtml(memory.content)}</div>
            </div>`).join("")
        : "";
      memoryList.querySelectorAll("[data-memory-id]").forEach((button) => {
        button.addEventListener("click", () => deleteMemory(button));
      });
    }
  } catch (_) {
    if (request !== memoryRequestSequence) return;
    if (memoryList) memoryList.replaceChildren();
    if (dialogMemoryCount) dialogMemoryCount.textContent = "";
    status.textContent = "Saved memories could not be loaded.";
  }
}

const assistantPanels = [
  { panel: $("#agent-memory-dialog"), trigger: $("#agent-memory-trigger") },
  { panel: $("#agent-attach-dialog"), trigger: $("#agent-attach-jobs") },
  { panel: $("#agent-job-detail-pane"), get trigger() { return agentJobDetailTrigger; } },
];

function openAssistantPanel(id) {
  for (const { panel, trigger } of assistantPanels) {
    const selected = panel.id === id;
    panel.hidden = !selected;
    trigger?.setAttribute("aria-expanded", String(selected));
  }
  $("#agent-history").open = false;
}

function closeAssistantPanel(restoreFocus = true) {
  for (const { panel, trigger } of assistantPanels) {
    if (panel.hidden) continue;
    panel.hidden = true;
    trigger?.setAttribute("aria-expanded", "false");
    if (restoreFocus) trigger?.focus();
  }
}

function openAgentMemoryPanel() {
  if (!$("#agent-memory-dialog").hidden) return closeAssistantPanel();
  openAssistantPanel("agent-memory-dialog");
  void loadMemoryStatus();
  $("#close-agent-memory-dialog").focus();
}

async function deleteMemory(button) {
  const memoryId = button.dataset.memoryId;
  const text = button.closest(".agent-memory-item").querySelector(".agent-memory-text").textContent;
  const confirmed = await confirmDecision({ title: "Forget this detail?", message: text, confirmLabel: "Forget detail", destructive: true });
  if (!confirmed || button.disabled) return;
  button.disabled = true;
  try {
    const res = await fetch(`/api/v1/agent/memories/${memoryId}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Could not delete memory");
    await loadMemoryStatus();
    announceStatus("Memory forgotten.");
    if (!$("#agent-memory-dialog").hidden) $("#close-agent-memory-dialog").focus();
  } catch (err) {
    showToast(errorMessage(err, "Could not forget this detail."), { variant: "error" });
    button.disabled = false;
  }
}

function closeAgentJobDetail() {
  if (!$("#agent-job-detail-pane").hidden) closeAssistantPanel();
}

async function viewAgentJob(key, trigger = null) {
  const job = renderedAgentJobs.get(key);
  if (!job) return;
  const detail = $("#agent-job-detail");
  agentJobDetailTrigger?.setAttribute("aria-expanded", "false");
  agentJobDetailTrigger = trigger;
  detail.innerHTML = `<p class="loading-detail">Loading job details…</p>`;
  openAssistantPanel("agent-job-detail-pane");
  $("#close-agent-job-detail").focus();
  try {
    if (job.source === "waterloo_work") {
      const module = await import("./waterlooworks.js?v=20260902-shared-components-v1");
      const result = await module.loadWaterlooWorksJobDetail(String(job.job_id));
      if (trigger === agentJobDetailTrigger && !$("#agent-job-detail-pane").hidden) detail.innerHTML = result.html;
    } else {
      const module = await import("./jobs.js?v=20260902-shared-components-v1");
      const result = await module.loadPublicJobDetail(String(job.job_id));
      if (trigger === agentJobDetailTrigger && !$("#agent-job-detail-pane").hidden) detail.innerHTML = result.html;
    }
  } catch (error) {
    if (trigger !== agentJobDetailTrigger || $("#agent-job-detail-pane").hidden) return;
    closeAgentJobDetail();
    showToast(errorMessage(error, "Job details unavailable."), { variant: "error" });
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

async function refreshAgentBookmarkState() {
  try {
    await Promise.all([loadPublicBookmarks(), loadWaterlooWorksBookmarks()]);
    renderedAgentJobs.forEach((job, key) => updateAgentSaveButtons(key, job));
  } catch (_) {
    // Recommendation cards remain usable when Tracker state is unavailable.
  }
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

function renderCurrentJobOption() {
  const option = $("#agent-current-job-option");
  if (!option) return;
  const job = jobContextState.activeJobContext;
  const attached = job ? isContextAttached(job) : false;
  // An attached current job is already shown in the pinned list above it.
  option.hidden = !job || attached;
  option.innerHTML = job
    ? `<p>Currently viewed job</p>
       <div class="agent-attach-result">
         <div class="agent-attach-result-copy">
           <div class="agent-attach-result-title-row">
             <strong>${escapeHtml(job.title || "Untitled role")}</strong>
           </div>
           <span class="agent-attach-result-meta"><span class="agent-attach-result-meta-text">${escapeHtml(job.company || "Company not specified")}</span></span>
         </div>
         <button type="button" class="glass agent-attach-result-action" data-attach-current-job aria-label="${attached ? "Job already attached" : "Attach current job"}" title="${attached ? "Already attached" : "Attach current job"}" ${attached ? "disabled" : ""}>${attached ? "✓" : "+"}</button>
       </div>`
    : "";
}

function renderAttachSearchResults(jobs, message = "") {
  const results = $("#agent-attach-results");
  if (!results) return;
  const attachedKeys = new Set(attachedJobContexts.map(attachedContextKey));
  const attachedJobs = attachedJobContexts.map((job) => ({
    source: job.source || "public",
    job_id: String(job.id),
    title: job.title,
    company: job.company,
    location: job.location,
  }));
  const availableJobs = jobs.filter((job) => !attachedKeys.has(agentJobKey(job)));
  const visibleJobs = [...attachedJobs, ...availableJobs];
  if (!visibleJobs.length) {
    results.innerHTML = `<p class="muted-copy">${escapeHtml(message || "No matching jobs found.")}</p>`;
    return;
  }
  results.innerHTML = visibleJobs.map((job) => {
    const key = agentJobKey(job);
    attachSearchJobs.set(key, job);
    const source = job.source === "waterloo_work" ? "WaterlooWorks" : "Job Board";
    const meta = [job.company, job.location].filter(Boolean).join(" · ");
    const attached = isContextAttached({ id: job.job_id, source: job.source });
    const action = attached
      ? `<button type="button" class="glass agent-attach-result-action" data-remove-attached-job="${escapeHtml(key)}" aria-label="Detach ${escapeHtml(job.title || "attached job")}" title="Detach job">✓</button>`
      : `<button type="button" class="glass agent-attach-result-action" data-attach-job-key="${escapeHtml(key)}" aria-label="Attach ${escapeHtml(job.title || "this job")}" title="Attach this job">+</button>`;
    return `<div class="agent-attach-result" role="listitem">
      <div class="agent-attach-result-copy">
        <div class="agent-attach-result-title-row">
          <strong>${escapeHtml(job.title || "Untitled role")}</strong>
          <span class="agent-attach-result-arrow" aria-hidden="true">↗</span>
        </div>
        <span class="agent-attach-result-meta"><span class="tag">${escapeHtml(source)}</span><span class="agent-attach-result-meta-text">${escapeHtml(meta || "Company not specified")}</span></span>
      </div>
      ${action}
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

let attachmentSearchSequence = 0;

async function searchAttachJobs() {
  const request = ++attachmentSearchSequence;
  const query = $("#agent-attach-query")?.value.trim() || "";
  const form = $("#agent-attach-search-form");
  attachSearchJobs.clear();
  form.dispatchEvent(new CustomEvent("assistant:attachment-search", { detail: true }));
  renderAttachSearchResults([], "Searching Job Board and WaterlooWorks…");
  const publicParams = new URLSearchParams({ limit: String(ATTACH_SEARCH_LIMIT) });
  const waterlooParams = new URLSearchParams({ limit: String(ATTACH_SEARCH_LIMIT) });
  if (query) {
    publicParams.set("query", query);
    waterlooParams.set("query", query);
  }
  try {
    const searches = await Promise.allSettled([
      fetchAttachJobList(`/api/v1/jobs?${publicParams}`, "public"),
      fetchAttachJobList(`/api/v1/waterlooworks/jobs?${waterlooParams}`, "waterloo_work"),
    ]);
    if (request !== attachmentSearchSequence) return;
    const jobs = searches.flatMap((result) => result.status === "fulfilled" ? result.value : []);
    const failedCount = searches.filter((result) => result.status === "rejected").length;
    if (failedCount === searches.length) {
      const reasons = searches.map((result) => result.reason?.message).filter(Boolean).join("\n");
      showToast(reasons || "Both job sources were unavailable.", { variant: "error" });
    }
    const emptyMessage = failedCount === searches.length ? "No results loaded." : "No matching jobs found.";
    renderAttachSearchResults(jobs, emptyMessage);
  } finally {
    if (request === attachmentSearchSequence) {
      form.dispatchEvent(new CustomEvent("assistant:attachment-search", { detail: false }));
    }
  }
}

async function attachSelectedJob(key, button) {
  const summary = attachSearchJobs.get(key);
  if (!summary) return;
  const initiatedWithFocus = document.activeElement === button;
  button.disabled = true;
  button.textContent = "…";
  try {
    const endpoint = summary.source === "waterloo_work"
      ? `/api/v1/waterlooworks/jobs/${encodeURIComponent(summary.job_id)}`
      : `/api/v1/jobs/${encodeURIComponent(summary.job_id)}`;
    const response = await fetch(endpoint);
    const job = await response.json();
    if (!response.ok) throw new Error(job.detail || "Could not attach this job.");
    const context = summary.source === "waterloo_work"
      ? waterlooWorksJobContext(job)
      : publicJobContext(job);
    if (addAttachedJobContext(context)) {
      const restoreFocus = initiatedWithFocus && (document.activeElement === button || document.activeElement === document.body);
      renderAttachSearchResults([...attachSearchJobs.values()]);
      if (restoreFocus) focusAttachmentAction(key);
    } else {
      button.textContent = "+";
      button.disabled = false;
    }
  } catch (error) {
    showToast(errorMessage(error, "Job could not be attached."), { variant: "error" });
    button.disabled = false;
    button.textContent = "+";
  }
}

function openAttachDialog() {
  if (!$("#agent-attach-dialog").hidden) return closeAssistantPanel();
  openAssistantPanel("agent-attach-dialog");
  renderCurrentJobOption();
  $("#agent-attach-search-form").reset();
  renderAttachSearchResults([], "Loading recent jobs…");
  searchAttachJobs();
  $("#agent-attach-query")?.focus();
}

function resizeAgentInput() {
  const input = $("#agent-input");
  if (!input) return;
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 150)}px`;
}

$("#agent-form")?.addEventListener("submit", (event) => {
  event.preventDefault();
  if (activeStream) {
    // The Send button doubles as Stop: abort the stream and keep whatever
    // the user just typed so they can correct and resend.
    activeStream.abort();
    return;
  }
  const text = $("#agent-input").value.trim();
  if (!text) {
    showFieldError($("#agent-input"), "Write a message before sending.");
    return;
  }
  clearFieldError($("#agent-input"));
  sendAgentMessage(text);
  resizeAgentInput();
});

$("#agent-new-session")?.addEventListener("click", createNewSession);
$("#agent-attach-jobs")?.addEventListener("click", openAttachDialog);
$("#close-agent-attach-dialog")?.addEventListener("click", () => closeAssistantPanel());
$("#done-agent-attach-dialog")?.addEventListener("click", () => closeAssistantPanel());
document.addEventListener("assistant:leave", () => closeAssistantPanel(false));
document.addEventListener("keydown", (event) => {
  if (event.defaultPrevented || event.key !== "Escape" || document.querySelector("dialog:modal") || $("#agent-history").open) return;
  if ($("#agent-job-detail posting-menu [aria-expanded=true]")) return;
  if (assistantPanels.some(({ panel }) => !panel.hidden)) {
    event.preventDefault();
    closeAssistantPanel();
  }
});
$("#agent-attach-search-form")?.addEventListener("submit", (event) => {
  event.preventDefault();
  searchAttachJobs();
});
$("#agent-current-job-option")?.addEventListener("click", (event) => {
  if (!event.target.closest("[data-attach-current-job]")) return;
  if (attachActiveJobContext()) {
    const context = jobContextState.activeJobContext;
    attachSearchJobs.set(agentJobKey({ source: context.source, job_id: context.id }), {
      source: context.source,
      job_id: context.id,
      title: context.title,
      company: context.company,
      location: context.location,
    });
    renderCurrentJobOption();
    renderAttachSearchResults([...attachSearchJobs.values()]);
    focusAttachmentAction(attachedContextKey(context));
  }
});
$("#agent-attach-results")?.addEventListener("click", (event) => {
  const detachButton = event.target.closest("[data-remove-attached-job]");
  if (detachButton) {
    removeAttachedJob(detachButton.dataset.removeAttachedJob);
    return;
  }
  const button = event.target.closest("[data-attach-job-key]");
  if (button) attachSelectedJob(button.dataset.attachJobKey, button);
});
const historyMenu = $("#agent-history");
historyMenu.addEventListener("toggle", () => {
  if (historyMenu.open) renderSessionList();
});
document.addEventListener("click", (event) => {
  if (historyMenu.open && !historyMenu.contains(event.target) && !event.target.closest("dialog")) {
    historyMenu.open = false;
  }
});
historyMenu.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || !historyMenu.open) return;
  event.preventDefault();
  historyMenu.open = false;
  historyMenu.querySelector("summary").focus();
});
$("#agent-memory-trigger")?.addEventListener("click", openAgentMemoryPanel);
$("#close-agent-memory-dialog")?.addEventListener("click", () => closeAssistantPanel());
$("#agent-session-list")?.addEventListener("click", (event) => {
  const deleteButton = event.target.closest("[data-delete-session-id]");
  if (deleteButton) {
    event.stopPropagation();
    deleteConversation(deleteButton.dataset.deleteSessionId, deleteButton);
    return;
  }
  const sessionButton = event.target.closest("[data-session-id]");
  if (sessionButton) {
    historyMenu.open = false;
    $("#agent-input").focus();
    switchSession(sessionButton.dataset.sessionId);
  }
});
// Reflow a draft when the window or navigation rail changes the available width.
let composerWidth = 0;
new ResizeObserver(([entry]) => {
  if (entry.contentRect.width === composerWidth) return;
  composerWidth = entry.contentRect.width;
  if (composerWidth > 0) resizeAgentInput();
}).observe($("#agent-form"));
$("#agent-input")?.addEventListener("input", (event) => {
  clearFieldError(event.currentTarget);
  resizeAgentInput();
});
$("#agent-input")?.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("#agent-form")?.requestSubmit();
  }
});
$("#agent-chat")?.addEventListener("click", (event) => {
  const suggestion = event.target.closest("[data-agent-prompt]");
  const viewButton = event.target.closest("[data-agent-view-job]");
  const saveButton = event.target.closest("[data-agent-save-job]");
  if (suggestion) {
    clearFieldError($("#agent-input"));
    $("#agent-input").value = suggestion.dataset.agentPrompt;
    resizeAgentInput();
    $("#agent-input").focus();
  } else if (viewButton) {
    viewAgentJob(viewButton.dataset.agentViewJob, viewButton);
  } else if (saveButton) {
    saveAgentJob(saveButton.dataset.agentSaveJob, saveButton);
  }
});
$("#close-agent-job-detail")?.addEventListener("click", closeAgentJobDetail);
$("#agent-job-detail-pane")?.addEventListener("click", (event) => {
  if (event.target.closest("[data-ai-target]")) closeAgentJobDetail();
});
renderAgentWelcome();
restoreSession();
renderSessionList();
refreshAgentBookmarkState();
if (currentSessionId) {
  switchSession(currentSessionId);
} else {
  loadMemoryStatus();
}

export { attachActiveJobContext, updateComposer as updateContextChip, renderSessionList };
