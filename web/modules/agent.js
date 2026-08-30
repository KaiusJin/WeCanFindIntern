import { $, escapeHtml, renderMarkdown, fetchWithTimeout } from "./helpers.js";
import { validateAiConfig } from "./settings.js";
import { state } from "./jobs.js";

// =========================================================
// SECTION 8: AI AGENT CHAT
// =========================================================

let currentSessionId = null;
let sessionList = [];

const SESSION_STORAGE_KEY = "wecanfindintern_agent_session_v1";

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
  const ctx = state.activeJobContext;
  const chip = $("#agent-context-job");
  const empty = $("#agent-context-empty");
  if (!chip || !empty) return;
  if (ctx) {
    chip.hidden = false;
    empty.hidden = true;
    chip.textContent = `📌 ${ctx.title} @ ${ctx.company || "Company"}`;
  } else {
    chip.hidden = true;
    empty.hidden = false;
  }
}

function appendMessage(role, html) {
  const chat = $("#agent-chat");
  if (!chat) return;
  const el = document.createElement("div");
  el.className = `agent-message agent-${role}`;
  el.innerHTML = `<div class="agent-bubble">${html}</div>`;
  chat.appendChild(el);
  chat.scrollTop = chat.scrollHeight;
}

function clearChat() {
  const chat = $("#agent-chat");
  if (chat) chat.innerHTML = "";
}

function sessionLabel(session) {
  const title = session.title || "Untitled";
  return escapeHtml(title.length > 32 ? `${title.slice(0, 32)}…` : title);
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
      return `<button type="button" class="agent-session-chip${active}" data-session-id="${escapeHtml(session.id)}">${sessionLabel(session)}</button>`;
    })
    .join("");
  list.querySelectorAll("[data-session-id]").forEach((button) => {
    button.addEventListener("click", () => {
      switchSession(button.dataset.sessionId);
    });
  });
}

async function switchSession(sessionId) {
  currentSessionId = sessionId;
  persistSession();
  clearChat();
  await renderSessionList();
  loadMemoryStatus();
  try {
    const res = await fetch(`/api/v1/agent/sessions/${sessionId}/messages`);
    if (!res.ok) throw new Error("Could not load conversation");
    const messages = await res.json();
    messages.forEach((message) => {
      appendMessage(
        message.role === "user" ? "user" : "assistant",
        message.role === "user"
          ? escapeText(message.content)
          : renderMarkdown(message.content)
      );
    });
  } catch (err) {
    appendMessage("assistant", `<p class="md-p agent-error">⚠ ${escapeText(err.message)}</p>`);
  }
}

async function createNewSession() {
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

function renderRecommendationCards(toolCalls) {
  const call = (toolCalls || []).find(
    (item) => item.tool_name === "recommend_jobs" && item.status === "succeeded"
  );
  const recommendations = call?.result?.data?.recommendations;
  if (!Array.isArray(recommendations) || !recommendations.length) return;
  const safeUrl = (value) => {
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) ? url.href : "";
    } catch (_) {
      return "";
    }
  };
  const wrapper = document.createElement("div");
  wrapper.className = "agent-recommendation-grid";
  wrapper.innerHTML = recommendations.map((job) => {
    const url = safeUrl(job.application_url);
    const matched = (job.matched_skills || []).slice(0, 5);
    const reason = (job.reasons || [])[0] || "Limited matching evidence.";
    return `<article class="agent-recommendation-card">
      <div class="agent-recommendation-head">
        <span class="agent-recommendation-score">${escapeHtml(job.match_score)}%</span>
        <span class="tag">${escapeHtml(job.confidence || "unknown")}</span>
      </div>
      <h4>${escapeHtml(job.title || "Untitled")}</h4>
      <p class="agent-recommendation-company">${escapeHtml(job.company || "Unknown company")}</p>
      <p>${escapeHtml(job.location || "Location not specified")} · ${escapeHtml(job.work_mode || "unknown")}</p>
      <p class="agent-recommendation-reason">${escapeHtml(reason)}</p>
      <div class="agent-recommendation-skills">${matched.map((skill) => `<span class="tag">${escapeHtml(skill)}</span>`).join("")}</div>
      ${url ? `<a class="primary-button compact-button" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">View job</a>` : ""}
    </article>`;
  }).join("");
  $("#agent-chat").appendChild(wrapper);
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
  const ctx = state.activeJobContext;
  if (!ctx) return null;
  return { job: { id: ctx.id, title: ctx.title, company: ctx.company, jd: ctx.jd } };
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
    appendMessage("assistant", renderMarkdown(data.message.content));
    renderRecommendationCards(data.tool_calls);
    if (data.pending_approval) {
      renderApprovalCard(data.pending_approval);
    }
    updateContextChip();
    renderSessionList();
    loadMemoryStatus();
  } catch (err) {
    thinking.remove();
    appendMessage("assistant", `<p class="md-p agent-error">⚠ ${escapeText(err.message)}</p>`);
  }
}

async function loadPreferences() {
  try {
    const res = await fetch("/api/v1/agent/preferences");
    if (!res.ok) return;
    const prefs = await res.json();
    const set = (id, key) => {
      const el = $(id);
      if (el && prefs[key] != null) el.value = prefs[key];
    };
    set("#agent-pref-ltm", "LONG_TERM_MEMORY");
    set("#agent-pref-locations", "TARGET_LOCATIONS");
    set("#agent-pref-roles", "TARGET_ROLES");
    set("#agent-pref-work-mode", "WORK_MODE");
    set("#agent-pref-salary", "SALARY_RANGE");
    set("#agent-pref-language", "ANSWER_LANGUAGE");
  } catch (_) { }
}

async function savePreferences() {
  const updates = {
    LONG_TERM_MEMORY: $("#agent-pref-ltm")?.value,
    TARGET_LOCATIONS: $("#agent-pref-locations")?.value.trim(),
    TARGET_ROLES: $("#agent-pref-roles")?.value.trim(),
    WORK_MODE: $("#agent-pref-work-mode")?.value,
    SALARY_RANGE: $("#agent-pref-salary")?.value.trim(),
    ANSWER_LANGUAGE: $("#agent-pref-language")?.value.trim(),
  };
  for (const [key, value] of Object.entries(updates)) {
    if (!value) {
      await fetch(`/api/v1/agent/preferences/${key}`, { method: "DELETE" });
      continue;
    }
    const res = await fetch(`/api/v1/agent/preferences/${key}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      alert(data.detail || `Could not save ${key}`);
      return;
    }
  }
  const feedback = $("#agent-pref-feedback");
  if (feedback) {
    feedback.hidden = false;
    setTimeout(() => { feedback.hidden = true; }, 2500);
  }
}

async function loadMemoryStatus() {
  const status = $("#agent-memory-status");
  const memoryList = $("#agent-memory-list");
  if (!status) return;
  if (!currentSessionId) {
    status.textContent = "No conversation yet.";
    if (memoryList) memoryList.innerHTML = "";
    return;
  }
  try {
    const res = await fetch(`/api/v1/agent/sessions/${currentSessionId}/memory`);
    if (!res.ok) return;
    const data = await res.json();
    const summary = data.summary || {};
    const ltm = data.long_term_memory || {};
    status.textContent =
      `Summary v${summary.version || 0} (${summary.unsummarized_tokens ?? 0} unsummarized tokens) · ` +
      `${ltm.active_count ?? 0} memories · long-term memory ${ltm.enabled ? "on" : "off"}`;
    if (memoryList) {
      const memories = data.memories || [];
      memoryList.innerHTML = memories.length
        ? memories.map((memory) => `
            <div class="agent-memory-item">
              <div class="agent-memory-item-head">
                <span class="agent-memory-type">${escapeHtml(memory.memory_type)}</span>
                <span class="agent-memory-conf">conf ${Math.round((memory.confidence || 0) * 100)}%</span>
              </div>
              <div class="agent-memory-text">${escapeHtml(memory.content)}</div>
              <div class="agent-memory-item-head" style="margin-top:4px;margin-bottom:0;">
                <span></span>
                <button type="button" class="agent-memory-delete" data-memory-id="${escapeHtml(memory.id)}" title="Delete this memory">✕ delete</button>
              </div>
            </div>`).join("")
        : '<div class="agent-memory-status">No long-term memories yet.</div>';
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

$("#agent-form")?.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = $("#agent-input").value.trim();
  if (!text) return;
  sendAgentMessage(text);
});

$("#agent-new-session")?.addEventListener("click", createNewSession);
$("#agent-pref-save")?.addEventListener("click", savePreferences);

restoreSession();
renderSessionList();
loadPreferences();
if (currentSessionId) {
  switchSession(currentSessionId);
} else {
  loadMemoryStatus();
}

export { updateContextChip, renderSessionList };
