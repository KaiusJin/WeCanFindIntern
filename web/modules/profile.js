import { $, escapeHtml, showErrorDialog } from "./helpers.js?v=20260901-error-dialog-minimal-v1";

// =========================================================
// PROFILE WORKSPACE
// =========================================================

let profileData = null;
let profileSavedData = null;
let profileImportId = null;
let profileSaveTimer = null;
let profileSaveInFlight = false;
let profileSaveQueued = false;
let profileForceSaveQueued = false;
let profileDirty = false;
let autofillMode = "resume";

const profileConfigs = [
  ["education", "Education", "education", [["institution", "School"], ["degree", "Degree"], ["major", "Major"], ["specialization", "Specialization"], ["minor", "Minor"], ["start_date_text", "Start date"], ["graduation_date_text", "Graduation date"], ["gpa", "GPA"], ["coursework", "Coursework", "list"]]],
  ["work_experience", "Work experience", "experience", [["company", "Company"], ["title", "Role"], ["location", "Location"], ["employment_type", "Type"], ["start_date_text", "Start date"], ["end_date_text", "End date"], ["description", "Description", "textarea"], ["skills", "Skills", "list"]]],
  ["projects", "Projects", "project", [["name", "Project name"], ["start_date_text", "Start date"], ["end_date_text", "End date"], ["project_url", "Project URL"], ["github_url", "GitHub URL"], ["skills", "Skills", "list"], ["description", "Description", "textarea"]]],
  ["skills", "Skills", "skill", [["name", "Skill name"]]],
  ["certifications", "Certifications", "certification", [["name", "Certification"], ["issuer", "Issuer"], ["issue_date_text", "Issue date"], ["expiry_date_text", "Expiry date"], ["credential_id", "Credential ID"], ["credential_url", "Credential URL"]]],
  ["languages", "Languages", "language", [["name", "Language"], ["proficiency", "Proficiency", "language-level"]]],
  ["awards", "Awards", "award", [["title", "Award"], ["issuer", "Issuer"], ["date_text", "Date"], ["description", "Description", "textarea"]]],
];

function emptyProfile() { return { schema_version: "profile.v1", basics: {}, education: [], work_experience: [], projects: [], skills: [], certifications: [], languages: [], awards: [] }; }
function fieldValue(item, field, type) { const value = item?.[field]; if (type === "list") return Array.isArray(value) ? value.join(", ") : value || ""; if (type === "lines") return Array.isArray(value) ? value.join("\n") : value || ""; return value ?? ""; }

function renderProfileSections() {
  $("#profile-repeat-sections").innerHTML = profileConfigs.map(([key, title, singular, fields]) => {
    const items = profileData[key] || [];
    const cards = items.map((item, index) => {
      const controls = fields.map(([field, label, type = "text"]) => {
        const value = escapeHtml(String(fieldValue(item, field, type)));
        const wide = ["textarea", "lines"].includes(type) ? " profile-item-wide" : "";
        const levels = ["Beginner", "Intermediate", "Advanced", "Fluent", "Native"];
        const input = type === "language-level" ? `<select class="career-input" data-profile-field="${field}" data-profile-type="${type}"><option value="">Select proficiency</option>${levels.map((level) => `<option value="${level}"${value === level ? " selected" : ""}>${level}</option>`).join("")}</select>` : ["textarea", "lines"].includes(type) ? `<textarea class="career-textarea" rows="3" data-profile-field="${field}" data-profile-type="${type}">${value}</textarea>` : `<input class="career-input" type="text" data-profile-field="${field}" data-profile-type="${type}" value="${value}" />`;
        return `<label class="${wide}"><span>${label}</span>${input}</label>`;
      }).join("");
      if (key === "skills") return `<article class="profile-item-card profile-skill-card" data-profile-section="${key}" data-profile-index="${index}"><div class="profile-item-grid">${controls}</div><button class="profile-remove-item profile-skill-remove" type="button" aria-label="Remove ${escapeHtml(item.name || "skill")}" title="Remove ${escapeHtml(item.name || "skill")}">−</button></article>`;
      return `<article class="profile-item-card" data-profile-section="${key}" data-profile-index="${index}"><div class="profile-item-head"><strong>${title} ${index + 1}</strong><button class="profile-remove-item profile-remove-item-button" type="button" aria-label="Remove ${title} ${index + 1}" title="Remove ${title} ${index + 1}">−</button></div><div class="profile-item-grid">${controls}</div></article>`;
    }).join("") || `<div class="profile-section-empty">No ${title.toLowerCase()} added yet.</div>`;
    return `<section class="profile-section-card profile-section-${key}"><div class="profile-section-heading"><div><h3>${title}</h3><small>${items.length}</small></div><button class="profile-add-item" data-profile-add="${key}" type="button" aria-label="Add ${singular}" title="Add ${singular}">+</button></div><div class="profile-items">${cards}</div></section>`;
  }).join("");
}

const basicFields = { "profile-full-name": "full_name", "profile-preferred-name": "preferred_name", "profile-email": "email", "profile-phone": "phone", "profile-city": "city", "profile-region": "region", "profile-country": "country", "profile-linkedin": "linkedin_url", "profile-github": "github_url", "profile-portfolio": "portfolio_url" };

function renderProfile(payload, completion = null) {
  profileData = payload || emptyProfile();
  Object.entries(basicFields).forEach(([id, field]) => { $(`#${id}`).value = profileData.basics?.[field] || ""; });
  renderProfileSections();
  const checks = [profileData.basics?.full_name, profileData.basics?.email, ...profileConfigs.map(([key]) => profileData[key]?.length)];
  const percent = completion ?? Math.round(checks.filter(Boolean).length / checks.length * 100);
  updateProfileCompletion(percent);
}

function updateProfileCompletion(percent) {
  $("#profile-completion-label").textContent = `${percent}% complete`;
  const fill = $("#profile-progress-fill");
  fill.style.width = `${percent}%`;
  fill.classList.toggle("profile-progress-has-value", percent > 0);
  $(".profile-progress")?.setAttribute("aria-valuenow", String(percent));
}

function collectProfile() {
  const payload = JSON.parse(JSON.stringify(profileData || emptyProfile())); payload.basics ||= {};
  Object.entries(basicFields).forEach(([id, field]) => { payload.basics[field] = $(`#${id}`).value.trim() || null; }); payload.basics.full_name ||= "";
  const required = { education: "institution", work_experience: "company", projects: "name", skills: "name", certifications: "name", languages: "name", awards: "title" };
  profileConfigs.forEach(([key]) => {
    payload[key] = [];
    document.querySelectorAll(`[data-profile-section="${key}"]`).forEach((card) => {
      const item = { ...(profileData[key]?.[Number(card.dataset.profileIndex)] || {}) };
      card.querySelectorAll("[data-profile-field]").forEach((input) => { const raw = input.value.trim(); const field = input.dataset.profileField; if (input.dataset.profileType === "list") item[field] = raw ? raw.split(",").map((v) => v.trim()).filter(Boolean) : []; else if (input.dataset.profileType === "lines") item[field] = raw ? raw.split("\n").map((v) => v.trim()).filter(Boolean) : []; else item[field] = raw || null; });
      item[required[key]] ??= "";
      if (key === "skills") {
        Object.keys(item).filter((field) => !["id", "name"].includes(field)).forEach((field) => delete item[field]);
        if (!item.name) return;
      }
      payload[key].push(item);
    });
  });
  return payload;
}

function showProfileStatus(message) { const box = $("#profile-import-status"); box.hidden = false; box.textContent = message; }

async function loadProfileWorkspace() {
  try { const response = await fetch("/api/v1/profile"); if (!response.ok) throw new Error("Could not load profile."); const loaded = await response.json(); profileSavedData = loaded; if (!profileImportId) renderProfile(loaded, loaded.completion_percent); } catch (error) { showErrorDialog(error, { title: "Profile unavailable" }); }
}

function mergeProfileDraft(saved, draft) { const merged = JSON.parse(JSON.stringify(saved || emptyProfile())); merged.basics = { ...(saved?.basics || {}), ...Object.fromEntries(Object.entries(draft.basics || {}).filter(([, value]) => value != null && value !== "")) }; profileConfigs.forEach(([key]) => { if (draft[key]?.length) merged[key] = draft[key]; }); merged.schema_version = "profile.v1"; return merged; }

function openAutofillDialog(mode) {
  autofillMode = mode;
  const dialog = $("#profile-autofill-dialog");
  const resumePanel = $("#profile-autofill-resume-panel");
  const latexPanel = $("#profile-autofill-latex-panel");
  const title = $("#profile-autofill-title");
  const guidance = $("#profile-autofill-guidance");
  const submit = $("#profile-autofill-submit");
  if (!dialog || !resumePanel || !latexPanel || !title || !guidance || !submit) return;
  const isResume = mode === "resume";
  title.textContent = `Autofill From ${isResume ? "resume" : "LaTeX"}`;
  guidance.textContent = isResume
    ? "Choose a text-based PDF resume. We’ll prepare a draft for review; your saved profile will not change until you apply the import."
    : "Paste your LaTeX source below. It will be parsed as text only; no commands are compiled or executed. Review the draft before applying it.";
  submit.textContent = `Autofill From ${isResume ? "resume" : "LaTeX"}`;
  resumePanel.hidden = !isResume;
  latexPanel.hidden = isResume;
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
}

function closeAutofillDialog() {
  const dialog = $("#profile-autofill-dialog");
  if (!dialog) return;
  if (typeof dialog.close === "function") dialog.close();
  else dialog.removeAttribute("open");
}

async function parseProfileFile(file, sourceType = autofillMode) {
  if (!file) return showErrorDialog("No resume file was selected.", { title: "Resume required", guidance: "Choose a PDF resume, then try again." });
  const submitButton = $("#profile-autofill-submit");
  const triggerButtons = [$("#profile-autofill-resume"), $("#profile-autofill-latex"), submitButton].filter(Boolean);
  triggerButtons.forEach((button) => { button.disabled = true; });
  if (submitButton) submitButton.textContent = "Autofilling…";
  showProfileStatus(`Importing and validating ${file.name}…`);
  try {
    const form = new FormData(); form.append("file", file, file.name);
    const response = await fetch("/api/v1/profile/resumes", { method: "POST", body: form });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) { showErrorDialog(result.detail || "Resume import failed.", { title: "Resume import failed" }); return; }
    profileImportId = result.import_id; profileDirty = false; renderProfile(mergeProfileDraft(profileSavedData, result.draft));
    $("#profile-draft-banner").hidden = false; $("#profile-editor-title").textContent = "Review imported profile";
    $("#profile-import-status").hidden = true;
    closeAutofillDialog();
  } catch (error) {
    showErrorDialog(error, { title: "Resume import failed" });
  } finally {
    triggerButtons.forEach((button) => { button.disabled = false; });
    if (submitButton) submitButton.textContent = `Autofill From ${sourceType === "latex" ? "LaTeX" : "resume"}`;
  }
}

function scheduleProfileAutosave({ immediate = false } = {}) {
  profileDirty = true;
  clearTimeout(profileSaveTimer);
  if (profileSaveInFlight) {
    profileSaveQueued = true;
    return;
  }
  profileSaveTimer = setTimeout(() => { void saveProfileWorkspace(); }, immediate ? 0 : 650);
}

async function saveProfileWorkspace({ force = false } = {}) {
  if (profileSaveInFlight) {
    profileSaveQueued = true;
    if (force) {
      profileDirty = true;
      profileForceSaveQueued = true;
    }
    return;
  }
  if (!force && !profileDirty) return;

  clearTimeout(profileSaveTimer);
  profileSaveTimer = null;
  profileSaveInFlight = true;
  profileSaveQueued = false;
  profileDirty = false;

  const payload = collectProfile();
  const importId = profileImportId;
  const confirmingImport = Boolean(importId && force);
  const url = importId
    ? (confirmingImport ? `/api/v1/profile/imports/${importId}/confirm` : `/api/v1/profile/imports/${importId}`)
    : "/api/v1/profile";
  try {
    const response = await fetch(url, {
      method: confirmingImport ? "POST" : "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || "Could not save profile.");
    if (confirmingImport && profileImportId === importId) {
      profileImportId = null;
      $("#profile-draft-banner").hidden = true;
      $("#profile-editor-title").textContent = "Your profile";
    }
    profileData = result;
    if (!importId || confirmingImport) profileSavedData = result;
    if (result.completion_percent != null) updateProfileCompletion(result.completion_percent);
    const status = $("#profile-import-status");
    if (confirmingImport) {
      status.hidden = true;
    } else if (importId) {
      status.hidden = false;
      status.textContent = "Import draft changes saved. Apply the draft when review is complete.";
    }
  } catch (error) {
    profileDirty = true;
    showErrorDialog(error, { title: "Profile changes could not be saved" });
  } finally {
    profileSaveInFlight = false;
    const queuedForce = profileForceSaveQueued;
    profileForceSaveQueued = false;
    if (profileSaveQueued) {
      profileSaveQueued = false;
      if (queuedForce) void saveProfileWorkspace({ force: true });
      else scheduleProfileAutosave({ immediate: true });
    }
  }
}

$("#profile-repeat-sections")?.addEventListener("click", (event) => { const add = event.target.closest("[data-profile-add]"); if (add) { profileData = collectProfile(); profileData[add.dataset.profileAdd].push({}); renderProfile(profileData); return; } const remove = event.target.closest(".profile-remove-item"); if (remove) { const card = remove.closest("[data-profile-section]"); profileData = collectProfile(); profileData[card.dataset.profileSection].splice(Number(card.dataset.profileIndex), 1); renderProfile(profileData); scheduleProfileAutosave({ immediate: true }); } });
$(".profile-editor-panel")?.addEventListener("input", (event) => {
  if (event.target.matches("input, textarea, select")) scheduleProfileAutosave();
});
$(".profile-editor-panel")?.addEventListener("change", (event) => {
  if (event.target.matches("input, textarea, select")) scheduleProfileAutosave({ immediate: true });
});
$(".profile-editor-panel")?.addEventListener("focusout", (event) => {
  if (event.target.matches("input, textarea, select") && profileDirty) scheduleProfileAutosave({ immediate: true });
});
$("#profile-autofill-resume")?.addEventListener("click", () => openAutofillDialog("resume"));
$("#profile-autofill-latex")?.addEventListener("click", () => openAutofillDialog("latex"));
$("#profile-resume-file-picker")?.addEventListener("click", () => $("#profile-resume-file")?.click());
$("#profile-resume-file")?.addEventListener("change", (event) => {
  $("#profile-resume-file-name").textContent = event.target.files?.[0]?.name || "No file selected";
});
$("#profile-autofill-submit")?.addEventListener("click", () => {
  if (autofillMode === "resume") {
    parseProfileFile($("#profile-resume-file").files?.[0], "resume");
    return;
  }
  parseProfileFile(new File([$("#profile-latex-source").value], "pasted-resume.tex", { type: "application/x-tex" }), "latex");
});
$("#profile-autofill-close")?.addEventListener("click", closeAutofillDialog);
$("#profile-autofill-dialog")?.addEventListener("click", (event) => {
  if (event.target === event.currentTarget) closeAutofillDialog();
});
$("#profile-apply-draft")?.addEventListener("click", () => { profileDirty = true; void saveProfileWorkspace({ force: true }); });
$("#profile-discard-draft")?.addEventListener("click", () => { clearTimeout(profileSaveTimer); profileDirty = false; profileImportId = null; $("#profile-draft-banner").hidden = true; $("#profile-editor-title").textContent = "Your profile"; renderProfile(profileSavedData || emptyProfile(), profileSavedData?.completion_percent); });

export { loadProfileWorkspace };
