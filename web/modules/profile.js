import { $, $$, escapeHtml } from "./helpers.js";

// =========================================================
// PROFILE WORKSPACE
// =========================================================

let profileData = null;
let profileSavedData = null;
let profileImportId = null;

const profileConfigs = [
  ["education", "02", "Education", "education", [["institution", "School"], ["degree", "Degree"], ["major", "Major"], ["specialization", "Specialization"], ["minor", "Minor"], ["start_date_text", "Start date"], ["graduation_date_text", "Graduation date"], ["gpa", "GPA"], ["coursework", "Coursework", "list"]]],
  ["work_experience", "03", "Work experience", "experience", [["company", "Company"], ["title", "Role"], ["location", "Location"], ["employment_type", "Type"], ["start_date_text", "Start date"], ["end_date_text", "End date"], ["description", "Description", "textarea"], ["skills", "Skills", "list"]]],
  ["projects", "04", "Projects", "project", [["name", "Project name"], ["start_date_text", "Start date"], ["end_date_text", "End date"], ["project_url", "Project URL"], ["github_url", "GitHub URL"], ["skills", "Skills", "list"], ["description", "Description", "textarea"]]],
  ["skills", "05", "Skills", "skill", [["name", "Skill name"]]],
  ["certifications", "06", "Certifications", "certification", [["name", "Certification"], ["issuer", "Issuer"], ["issue_date_text", "Issue date"], ["expiry_date_text", "Expiry date"], ["credential_id", "Credential ID"], ["credential_url", "Credential URL"]]],
  ["languages", "07", "Languages", "language", [["name", "Language"], ["proficiency", "Proficiency", "language-level"]]],
  ["awards", "08", "Awards", "award", [["title", "Award"], ["issuer", "Issuer"], ["date_text", "Date"], ["description", "Description", "textarea"]]],
];

function emptyProfile() { return { schema_version: "profile.v1", basics: {}, education: [], work_experience: [], projects: [], skills: [], certifications: [], languages: [], awards: [] }; }
function fieldValue(item, field, type) { const value = item?.[field]; if (type === "list") return Array.isArray(value) ? value.join(", ") : value || ""; if (type === "lines") return Array.isArray(value) ? value.join("\n") : value || ""; return value ?? ""; }

function renderProfileSections() {
  $("#profile-repeat-sections").innerHTML = profileConfigs.map(([key, number, title, singular, fields]) => {
    const items = profileData[key] || [];
    const cards = items.map((item, index) => {
      const controls = fields.map(([field, label, type = "text"]) => {
        const value = escapeHtml(String(fieldValue(item, field, type)));
        const wide = ["textarea", "lines"].includes(type) ? " profile-item-wide" : "";
        const levels = ["Beginner", "Intermediate", "Advanced", "Fluent", "Native"];
        const input = type === "language-level" ? `<select class="career-input" data-profile-field="${field}" data-profile-type="${type}"><option value="">Select proficiency</option>${levels.map((level) => `<option value="${level}"${value === level ? " selected" : ""}>${level}</option>`).join("")}</select>` : ["textarea", "lines"].includes(type) ? `<textarea class="career-textarea" rows="3" data-profile-field="${field}" data-profile-type="${type}">${value}</textarea>` : `<input class="career-input" type="text" data-profile-field="${field}" data-profile-type="${type}" value="${value}" />`;
        return `<label class="${wide}"><span>${label}</span>${input}</label>`;
      }).join("");
      const confidence = item.confidence == null ? "" : `<span class="profile-confidence">${Math.round(item.confidence * 100)}% confidence</span>`;
      if (key === "skills") return `<article class="profile-item-card profile-skill-card" data-profile-section="${key}" data-profile-index="${index}"><div class="profile-item-grid">${controls}</div><button class="profile-remove-item profile-skill-remove" type="button" aria-label="Remove ${escapeHtml(item.name || "skill")}">×</button></article>`;
      return `<article class="profile-item-card" data-profile-section="${key}" data-profile-index="${index}"><div class="profile-item-head"><strong>${title} ${index + 1}</strong><div>${confidence}<button class="profile-remove-item text-button danger-text" type="button">Remove</button></div></div><div class="profile-item-grid">${controls}</div></article>`;
    }).join("") || `<div class="profile-section-empty">No ${title.toLowerCase()} added yet.</div>`;
    return `<section class="profile-section-card profile-section-${key}"><div class="profile-section-heading"><div><span>${number}</span><h3>${title}</h3><small>${items.length}</small></div><button class="secondary-button compact-button" data-profile-add="${key}" type="button">+ Add ${singular}</button></div><div class="profile-items">${cards}</div></section>`;
  }).join("");
}

const basicFields = { "profile-full-name": "full_name", "profile-preferred-name": "preferred_name", "profile-email": "email", "profile-phone": "phone", "profile-city": "city", "profile-region": "region", "profile-country": "country", "profile-linkedin": "linkedin_url", "profile-github": "github_url", "profile-portfolio": "portfolio_url" };

function renderProfile(payload, completion = null) {
  profileData = payload || emptyProfile();
  Object.entries(basicFields).forEach(([id, field]) => { $(`#${id}`).value = profileData.basics?.[field] || ""; });
  renderProfileSections();
  const checks = [profileData.basics?.full_name, profileData.basics?.email, ...profileConfigs.map(([key]) => profileData[key]?.length)];
  const percent = completion ?? Math.round(checks.filter(Boolean).length / checks.length * 100);
  $("#profile-completion-label").textContent = `${percent}% complete`; $("#profile-progress-fill").style.width = `${percent}%`;
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
      item[required[key]] ??= ""; if (key === "skills") Object.keys(item).filter((field) => field !== "name").forEach((field) => delete item[field]);
      payload[key].push(item);
    });
  });
  return payload;
}

function showProfileStatus(message, error = false) { const box = $("#profile-import-status"); box.hidden = false; box.textContent = message; box.classList.toggle("error", error); }
function renderResumeHistory(items) { $("#profile-resume-list").innerHTML = items.length ? items.map((item) => `<article class="profile-resume-row"><div><strong>${escapeHtml(item.filename)}</strong><span>${item.source_type.toUpperCase()} · ${(item.size_bytes / 1024).toFixed(0)} KB · ${item.status}</span></div><button class="text-button danger-text profile-delete-resume" data-resume-id="${item.id}" type="button">Delete</button></article>`).join("") : `<p class="muted-copy">No resumes uploaded yet.</p>`; }

async function loadProfileWorkspace() {
  try { const [p, r] = await Promise.all([fetch("/api/v1/profile"), fetch("/api/v1/profile/resumes")]); if (!p.ok) throw new Error("Could not load profile."); const loaded = await p.json(); profileSavedData = loaded; if (!profileImportId) renderProfile(loaded, loaded.completion_percent); renderResumeHistory(r.ok ? await r.json() : []); } catch (error) { showProfileStatus(error.message, true); }
}

function mergeProfileDraft(saved, draft) { const merged = JSON.parse(JSON.stringify(saved || emptyProfile())); merged.basics = { ...(saved?.basics || {}), ...Object.fromEntries(Object.entries(draft.basics || {}).filter(([, value]) => value != null && value !== "")) }; profileConfigs.forEach(([key]) => { if (draft[key]?.length) merged[key] = draft[key]; }); merged.schema_version = "profile.v1"; return merged; }

async function parseProfileFile(file) {
  if (!file) return showProfileStatus("Choose a PDF or .tex file first.", true);
  const uploadButton = $("#profile-upload-file");
  const latexButton = $("#profile-parse-latex");
  uploadButton.disabled = true; latexButton.disabled = true;
  uploadButton.textContent = "Parsing resume…";
  showProfileStatus(`Uploading and validating ${file.name}…`);
  try {
    const form = new FormData(); form.append("file", file, file.name);
    const response = await fetch("/api/v1/profile/resumes", { method: "POST", body: form });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) { showProfileStatus(result.detail || "Resume import failed.", true); return; }
    profileImportId = result.import_id; renderProfile(mergeProfileDraft(profileSavedData, result.draft));
    $("#profile-draft-banner").hidden = false; $("#profile-editor-title").textContent = "Review imported profile";
    showProfileStatus(`Parsed ${result.resume.filename}. Review the extracted fields, then save to confirm.`);
    const resumes = await fetch("/api/v1/profile/resumes"); if (resumes.ok) renderResumeHistory(await resumes.json());
  } catch (error) {
    showProfileStatus(`Upload failed: ${error.message}`, true);
  } finally {
    uploadButton.disabled = false; latexButton.disabled = false;
    uploadButton.textContent = "Parse selected resume";
  }
}

async function saveProfileWorkspace() {
  const payload = collectProfile(); const url = profileImportId ? `/api/v1/profile/imports/${profileImportId}/confirm` : "/api/v1/profile";
  const response = await fetch(url, { method: profileImportId ? "POST" : "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }); const result = await response.json();
  if (!response.ok) return showProfileStatus(result.detail || "Could not save profile.", true);
  profileImportId = null; profileSavedData = result; $("#profile-draft-banner").hidden = true; $("#profile-editor-title").textContent = "Your profile"; renderProfile(result, result.completion_percent); $("#profile-save-feedback").hidden = false; setTimeout(() => { $("#profile-save-feedback").hidden = true; }, 1600); showProfileStatus("Profile saved.");
}

$("#profile-repeat-sections")?.addEventListener("click", (event) => { const add = event.target.closest("[data-profile-add]"); if (add) { profileData = collectProfile(); profileData[add.dataset.profileAdd].push({}); return renderProfile(profileData); } const remove = event.target.closest(".profile-remove-item"); if (remove) { const card = remove.closest("[data-profile-section]"); profileData = collectProfile(); profileData[card.dataset.profileSection].splice(Number(card.dataset.profileIndex), 1); renderProfile(profileData); } });
$("#profile-upload-file")?.addEventListener("click", () => parseProfileFile($("#profile-resume-file").files?.[0]));
$("#profile-resume-file")?.addEventListener("change", (event) => {
  const file = event.target.files?.[0];
  if (file) parseProfileFile(file);
});
$("#profile-parse-latex")?.addEventListener("click", () => parseProfileFile(new File([$("#profile-latex-source").value], "pasted-resume.tex", { type: "application/x-tex" })));
$("#profile-save")?.addEventListener("click", saveProfileWorkspace); $("#profile-save-bottom")?.addEventListener("click", saveProfileWorkspace); $("#profile-refresh-resumes")?.addEventListener("click", loadProfileWorkspace);
$("#profile-discard-draft")?.addEventListener("click", () => { profileImportId = null; $("#profile-draft-banner").hidden = true; $("#profile-editor-title").textContent = "Your profile"; renderProfile(profileSavedData || emptyProfile(), profileSavedData?.completion_percent); });
$("#profile-resume-list")?.addEventListener("click", async (event) => { const button = event.target.closest(".profile-delete-resume"); if (!button || !window.confirm("Delete this resume and its import draft?")) return; const response = await fetch(`/api/v1/profile/resumes/${button.dataset.resumeId}`, { method: "DELETE" }); if (response.ok) loadProfileWorkspace(); else showProfileStatus("Could not delete resume.", true); });

export { loadProfileWorkspace };
