import { $, errorMessage, showToast, showFieldError, clearFieldError, confirmDecision } from "./helpers.js?v=20260901-error-dialog-minimal-v1";
import { profileFieldValue, profileFormatError } from "./profile-fields.js";

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
const fieldsEditedBeforeChange = new WeakSet();
let profileChapter = "basics";

function setProfileSaveStatus(message, state = "") {
  const status = $("#profile-save-status");
  status.textContent = message;
  status.dataset.state = state;
}

const profileConfigs = [
  ["education", "Education", "education", [["institution", "School"], ["degree", "Degree"], ["major", "Major"], ["specialization", "Specialization"], ["minor", "Minor"], ["start_date_text", "Start date"], ["graduation_date_text", "Graduation date"], ["gpa", "GPA"], ["coursework", "Coursework", "list"]]],
  ["work_experience", "Work experience", "experience", [["company", "Company"], ["title", "Role"], ["location", "Location"], ["employment_type", "Type"], ["start_date_text", "Start date"], ["end_date_text", "End date"], ["description", "Description", "textarea"], ["skills", "Skills", "list"]]],
  ["projects", "Projects", "project", [["name", "Project name"], ["start_date_text", "Start date"], ["end_date_text", "End date"], ["project_url", "Project URL", "url"], ["github_url", "GitHub URL", "url"], ["skills", "Skills", "list"], ["description", "Description", "textarea"]]],
  ["skills", "Skills", "skill", [["name", "Skill name"]]],
  ["certifications", "Certifications", "certification", [["name", "Certification"], ["issuer", "Issuer"], ["issue_date_text", "Issue date"], ["expiry_date_text", "Expiry date"], ["credential_id", "Credential ID"], ["credential_url", "Credential URL", "url"]]],
  ["languages", "Languages", "language", [["name", "Language"], ["proficiency", "Proficiency", "language-level"]]],
  ["awards", "Awards", "award", [["title", "Award"], ["issuer", "Issuer"], ["date_text", "Date"], ["description", "Description", "textarea"]]],
];

function emptyProfile() { return { schema_version: "profile.v1", basics: {}, education: [], work_experience: [], projects: [], skills: [], certifications: [], languages: [], awards: [] }; }
function selectProfileChapter(key) {
  profileChapter = key;
  for (const tab of $("#profile-chapters").querySelectorAll("[role=tab]")) {
    const selected = tab.dataset.chapter === key;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  }
  for (const panel of $(".profile-document").querySelectorAll("[data-section]")) {
    panel.hidden = panel.dataset.section !== key;
  }
}

function setupProfileChapters() {
  const chapters = [["basics", "Personal details"], ...profileConfigs.map(([key, title]) => [key, title])];
  const tablist = $("#profile-chapters");
  tablist.innerHTML = chapters.map(([key, title]) =>
    `<button type="button" role="tab" id="profile-tab-${key}" data-chapter="${key}" aria-controls="profile-panel-${key}">${title}</button>`
  ).join("");
  tablist.addEventListener("click", (event) => {
    const tab = event.target.closest("[data-chapter]");
    if (tab) selectProfileChapter(tab.dataset.chapter);
  });
  tablist.addEventListener("keydown", (event) => {
    const tabs = Array.from(tablist.querySelectorAll("[role=tab]"));
    const index = tabs.indexOf(event.target);
    if (index < 0) return;
    const destinations = {
      ArrowRight: (index + 1) % tabs.length,
      ArrowLeft: (index - 1 + tabs.length) % tabs.length,
      Home: 0,
      End: tabs.length - 1,
    };
    if (!Object.hasOwn(destinations, event.key)) return;
    event.preventDefault();
    const tab = tabs[destinations[event.key]];
    selectProfileChapter(tab.dataset.chapter);
    tab.focus();
  });
  selectProfileChapter(profileChapter);
}

function updateProfileIdentity() {
  const name = $("#profile-preferred-name").value.trim() || $("#profile-full-name").value.trim();
  const location = ["city", "region", "country"]
    .map((field) => $(`#profile-${field}`).value.trim()).filter(Boolean).join(", ");
  $("#profile-display-name").textContent = name || "Your profile";
  const parts = name.split(/\s+/).filter(Boolean);
  const initials = parts.length > 1 ? [parts[0], parts.at(-1)] : parts;
  $("#profile-initials").textContent = initials.map((part) => Array.from(part)[0]).join("").toLocaleUpperCase();
  $("#profile-display-location").textContent = location;
  $("#profile-display-location").hidden = !location;
}

function renderProfileSections() {
  const sections = profileConfigs.map(([key, title, singular, fields]) => ({
    key, title, singular, fields, items: profileData[key] || [],
  }));
  $("#profile-repeat-sections").dispatchEvent(new CustomEvent("profile:replace-entries", {
    detail: { chapter: profileChapter, sections },
  }));
}

const basicFields = { "profile-full-name": "full_name", "profile-preferred-name": "preferred_name", "profile-email": "email", "profile-phone": "phone", "profile-city": "city", "profile-region": "region", "profile-country": "country", "profile-linkedin": "linkedin_url", "profile-github": "github_url", "profile-portfolio": "portfolio_url" };

function renderProfile(payload, completion = null) {
  document.querySelectorAll('.profile-document [aria-invalid="true"]').forEach(clearFieldError);
  profileData = payload || emptyProfile();
  const values = Object.fromEntries(Object.entries(basicFields).map(([id, field]) => [id, profileData.basics?.[field] || ""]));
  $("#profile-basics-fields").dispatchEvent(new CustomEvent("profile:replace-basics", { detail: values }));
  renderProfileSections();
  validateProfileFormats(false);
  updateProfileIdentity();
  updateProfileCompletion(completion);
}

function updateProfileCompletion(percent) {
  if (percent == null) {
    const checks = [profileData.basics?.full_name, profileData.basics?.email, ...profileConfigs.map(([key]) =>
      key === "skills" ? profileData.skills?.some(skill => skill.name?.trim()) : profileData[key]?.length
    )];
    percent = Math.round(checks.filter(Boolean).length / checks.length * 100);
  }
  const subject = profileImportId ? "Draft" : "Profile";
  const text = percent === 100 ? `${subject} complete` : `${subject} ${percent}% complete`;
  const label = $("#profile-completion-label");
  if (label.textContent !== text) label.textContent = text;
  const status = $(".profile-completion");
  status.toggleAttribute("data-complete", percent === 100);
  status.hidden = false;
}

function collectProfile({ keepEmptySkills = false, skillKeys } = {}) {
  const payload = JSON.parse(JSON.stringify(profileData || emptyProfile())); payload.basics ||= {};
  for (const [id, field] of Object.entries(basicFields)) payload.basics[field] = profileFieldValue(document.getElementById(id));
  payload.basics.full_name ||= "";
  const required = { education: "institution", work_experience: "company", projects: "name", skills: "name", certifications: "name", languages: "name", awards: "title" };
  profileConfigs.forEach(([key]) => {
    payload[key] = [];
    document.querySelectorAll(`[data-profile-section="${key}"]`).forEach((card) => {
      if (key === "skills") {
        const name = card.querySelector("[data-profile-field]").value.trim();
        if (name || keepEmptySkills) {
          const skill = { name, id: card.dataset.profileSkillId || null };
          if (keepEmptySkills) skill.editorKey = card.dataset.profileSkillKey;
          skillKeys?.push(card.dataset.profileSkillKey);
          payload.skills.push(skill);
        }
        return;
      }
      const item = { ...(profileData[key]?.[Number(card.dataset.profileIndex)] || {}) };
      for (const input of card.querySelectorAll("[data-profile-field]")) {
        item[input.dataset.profileField] = profileFieldValue(input);
      }
      item[required[key]] ??= "";
      payload[key].push(item);
    });
  });
  return payload;
}

async function loadProfileWorkspace() {
  try {
    const response = await fetch("/api/v1/profile");
    if (!response.ok) throw new Error("Could not load profile.");
    const loaded = await response.json();
    if (profileDirty || profileSaveInFlight) return;
    profileSavedData = loaded;
    if (!profileImportId) {
      renderProfile(loaded, loaded.completion_percent);
      setProfileSaveStatus("All changes saved", "saved");
    }
  } catch (error) {
    showToast(errorMessage(error, "Profile unavailable."), { variant: "error" });
  }
}

function mergeProfileDraft(saved, draft) { const merged = JSON.parse(JSON.stringify(saved || emptyProfile())); merged.basics = { ...(saved?.basics || {}), ...Object.fromEntries(Object.entries(draft.basics || {}).filter(([, value]) => value != null && value !== "")) }; profileConfigs.forEach(([key]) => { if (draft[key]?.length) merged[key] = draft[key]; }); merged.schema_version = "profile.v1"; return merged; }

async function parseProfileFile(file, mode) {
  const control = $(mode === "latex" ? "#profile-latex-source" : "#profile-resume-file-picker");
  const target = () => control?.isConnected && $("#profile-autofill-dialog").open;
  clearFieldError(control);
  if (!file || (mode === "latex" && !control.value.trim())) {
    showFieldError(control, mode === "latex" ? "Paste your LaTeX source to preview it." : "Choose a PDF resume to preview it.");
    return false;
  }
  try {
    const form = new FormData(); form.append("file", file, file.name);
    const response = await fetch("/api/v1/profile/resumes", { method: "POST", body: form });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = errorMessage(result.detail, "Resume import failed.");
      if (target() && [400, 413, 415, 422].includes(response.status)) showFieldError(control, message);
      else showToast(message, { variant: "error" });
      return false;
    }
    clearTimeout(profileSaveTimer);
    profileImportId = result.import_id;
    profileDirty = false;
    renderProfile(mergeProfileDraft(profileSavedData, result.draft));
    const imported = profileConfigs.filter(([key]) => result.draft[key]?.length);
    selectProfileChapter(Object.values(result.draft.basics || {}).some(Boolean) ? "basics" : imported[0]?.[0] || "basics");
    const sections = imported.map(([, title]) => title.toLowerCase());
    if (Object.values(result.draft.basics || {}).some(Boolean)) sections.unshift("basic information");
    const reviewCopy = sections.length ? `Review changes to ${sections.join(", ")}.` : "Review the fields before applying this import.";
    $("#profile-draft-summary").textContent = [reviewCopy, ...(result.warnings || [])].join(" ");
    $("#profile-draft-review").hidden = false;
    setProfileSaveStatus("Import ready to review");

    return true;
  } catch (error) {
    showToast(errorMessage(error, "Resume import failed."), { variant: "error" });
  }
  return false;
}

function markProfileField(input, message, focus) {
  // The note belongs below this glass surface inside its own grid cell.
  input.closest(".lg-input__field")?.setAttribute("data-field-control", "");
  if (focus) selectProfileChapter(input.closest("[data-section]").dataset.section);
  showFieldError(input, message, { focus });
}

function validateProfileFormats(reveal) {
  let valid = true;
  for (const input of document.querySelectorAll('.profile-document input:is([type="email"], [inputmode="url"])')) {
    const message = profileFormatError(input);
    if (message) {
      markProfileField(input, message, reveal && valid);
      valid = false;
    }
  }
  syncProfileInvalidTabs();
  return valid;
}

function syncProfileInvalidTabs() {
  for (const panel of document.querySelectorAll(".profile-document [role=tabpanel]")) {
    const tab = document.getElementById(panel.getAttribute("aria-labelledby"));
    const invalid = Boolean(panel.querySelector('[aria-invalid="true"]'));
    tab.toggleAttribute("data-has-errors", invalid);
    if (invalid) tab.setAttribute("aria-description", "Contains a field to review");
    else tab.removeAttribute("aria-description");
  }
}

function markProfileValidation(issues, reveal) {
  let marked = false;
  for (const issue of issues) {
    const [section, index, field] = (issue.loc || []).filter(part => part !== "body");
    const id = section === "basics" ? Object.keys(basicFields).find(id => basicFields[id] === index) : null;
    const input = id ? document.getElementById(id) : Number.isInteger(index) && field
      ? document.querySelector(`[data-profile-section="${CSS.escape(section)}"][data-profile-index="${index}"] [data-profile-field="${CSS.escape(field)}"]`)
      : null;
    if (!input) continue;
    markProfileField(input, issue.msg || "Check this value.", reveal && !marked);
    marked = true;
  }
  syncProfileInvalidTabs();
  return marked;
}

function scheduleProfileAutosave({ immediate = false } = {}) {
  profileDirty = true;
  setProfileSaveStatus(profileImportId ? "Unsaved draft changes" : "Unsaved changes");
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
  if (!validateProfileFormats(force)) {
    profileDirty = true;
    setProfileSaveStatus("Check highlighted fields · Changes not saved", "error");
    return;
  }
  profileSaveInFlight = true;
  profileSaveQueued = false;
  profileDirty = false;
  setProfileSaveStatus(profileImportId ? "Saving draft…" : "Saving…", "pending");

  const skillKeys = [];
  const payload = collectProfile({ skillKeys });
  const importId = profileImportId;
  const confirmingImport = Boolean(importId && force);
  if (confirmingImport) {
    $("#profile-apply-draft").disabled = true;
    $("#profile-discard-draft").disabled = true;
    $("#profile-apply-draft .lg-button__label").textContent = "Applying…";
  }
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
    if (!response.ok) {
      if (response.status === 422 && Array.isArray(result.detail)) {
        if (profileSaveQueued) return;
        if (markProfileValidation(result.detail, force)) {
          profileDirty = true;
          setProfileSaveStatus("Check highlighted fields · Changes not saved", "error");
          return;
        }
      }
      throw new Error(errorMessage(result.detail, "Could not save profile changes."));
    }
    if (profileImportId !== importId) {
      if (!importId) profileSavedData = result;
      return;
    }
    if (confirmingImport) {
      profileImportId = null;
      $("#profile-draft-review").hidden = true;
    }
    // A removed or blank tag can shift positions while this save is in flight.
    const skillIds = new Map(skillKeys.map((key, index) => [key, result.skills?.[index]?.id]));
    for (const tag of document.querySelectorAll("[data-profile-skill-key]")) {
      const id = skillIds.get(tag.dataset.profileSkillKey);
      if (id) tag.dataset.profileSkillId = id;
    }
    profileData = result;
    if (!importId || confirmingImport) profileSavedData = result;
    updateProfileCompletion(result.completion_percent);
    if (!importId || confirmingImport) document.dispatchEvent(new Event("wcfi:profile-saved"));
    if (confirmingImport) {
      setProfileSaveStatus(profileDirty || profileSaveQueued ? "Import applied · Unsaved changes" : "Import applied · All changes saved", profileDirty || profileSaveQueued ? "" : "saved");
      $("#profile-autofill-resume").focus();
    } else if (!profileDirty && !profileSaveQueued) {
      setProfileSaveStatus(importId ? "Draft saved · Apply import to update your profile" : "All changes saved", "saved");
    }
  } catch (error) {
    profileDirty = true;
    setProfileSaveStatus("Changes not saved", "error");
    showToast(errorMessage(error, "Could not save profile changes."), { variant: "error" });
  } finally {
    profileSaveInFlight = false;
    if (confirmingImport) {
      $("#profile-apply-draft .lg-button__label").textContent = "Apply import";
      $("#profile-apply-draft").disabled = false;
      $("#profile-discard-draft").disabled = false;
    }
    const queuedForce = profileForceSaveQueued;
    profileForceSaveQueued = false;
    if (profileSaveQueued) {
      profileSaveQueued = false;
      if (queuedForce) void saveProfileWorkspace({ force: true });
      else scheduleProfileAutosave({ immediate: true });
    }
  }
}

$("#profile-repeat-sections")?.addEventListener("click", async (event) => {
  const add = event.target.closest("[data-profile-add]");
  const remove = event.target.closest(".profile-remove-item");
  if (!add && !remove) return;
  const key = (add || remove).closest("[data-section]").dataset.section;
  if (add && key === "skills") {
    const empty = [...document.querySelectorAll(".profile-skill-tag input")].find(input => !input.value.trim());
    if (empty) {
      empty.focus();
      return;
    }
  }
  if (remove) {
    const row = remove.closest("[data-profile-index]");
    const values = [...row.querySelectorAll("[data-profile-field]")].map(input => input.value.trim());
    if (values.some(Boolean)) {
      const name = profileConfigs.find(([section]) => section === key)[2];
      const confirmed = await confirmDecision({
        title: `Remove this ${name}?`,
        message: `“${values.find(Boolean)}” will be removed from ${profileImportId ? "this import draft" : "your profile"}.`,
        confirmLabel: "Remove", destructive: true,
      });
      if (!confirmed || !row.isConnected) return;
    }
  }
  profileData = collectProfile({ keepEmptySkills: true });
  if (add) profileData[key].push({});
  else profileData[key].splice(Number(remove.closest("[data-profile-index]").dataset.profileIndex), 1);
  renderProfile(profileData);
  if (add) {
    const entries = document.querySelectorAll(`[data-profile-section="${key}"]`);
    entries[entries.length - 1].querySelector("[data-profile-field]").focus();
  } else {
    $(`[data-profile-add="${key}"]`).focus();
    scheduleProfileAutosave({ immediate: true });
  }
});
setupProfileChapters();

$(".profile-editor-panel")?.addEventListener("input", (event) => {
  if (event.target.closest(".profile-basics")) updateProfileIdentity();
  if (event.target.closest(".profile-document") && event.target.matches("input, textarea, select")) {
    fieldsEditedBeforeChange.add(event.target);
    scheduleProfileAutosave();
  }
});
$(".profile-editor-panel")?.addEventListener("change", (event) => {
  if (!event.target.closest(".profile-document") || !event.target.matches("input, textarea, select")) return;
  if (fieldsEditedBeforeChange.delete(event.target) && !profileDirty) return;
  scheduleProfileAutosave({ immediate: true });
});
$(".profile-editor-panel")?.addEventListener("focusout", (event) => {
  if (event.target.closest(".profile-document") && event.target.matches("input, textarea, select") && profileDirty) scheduleProfileAutosave({ immediate: true });
});
$("#profile-autofill-dialog")?.addEventListener("input", (event) => {
  if (event.target.id === "profile-latex-source") clearFieldError(event.target);
  if (event.target.id === "profile-resume-file") clearFieldError($("#profile-resume-file-picker"));
});
$("#profile-autofill-dialog")?.dispatchEvent(new CustomEvent("profile:import-ready", { detail: parseProfileFile }));
$("#profile-apply-draft")?.addEventListener("click", () => { profileDirty = true; void saveProfileWorkspace({ force: true }); });
$("#profile-discard-draft")?.addEventListener("click", async () => {
  const id = profileImportId;
  const discard = await confirmDecision({ title: "Discard this import?", message: "Your saved profile will be restored and edits to this draft will be discarded.", confirmLabel: "Discard draft", destructive: true });
  if (!discard || !id || profileImportId !== id) return;
  clearTimeout(profileSaveTimer);
  profileDirty = false;
  profileImportId = null;
  $("#profile-draft-review").hidden = true;
  setProfileSaveStatus("Import discarded · Saved profile restored", "saved");
  renderProfile(profileSavedData || emptyProfile(), profileSavedData?.completion_percent);
  $("#profile-autofill-resume").focus();
});

export { loadProfileWorkspace };
