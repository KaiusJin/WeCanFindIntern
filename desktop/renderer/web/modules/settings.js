import { $, showToast, showSystemFault, showFieldError, clearFieldError, createActionFeedback } from "./helpers.js?v=20260901-error-dialog-minimal-v1";

// =========================================================
// GLOBAL AI SETTINGS & LOCALSTORAGE
// =========================================================

const SETTINGS_STORAGE_KEY = "wecanfindintern_ai_settings_v3";
const PROVIDER_KEY_FIELDS = {
  DeepSeek: "deepseekKey",
  Gemini: "geminiKey",
  OpenAI: "openaiKey",
  GLM: "glmKey",
  Qwen: "qwenKey",
};
const SECRET_FIELDS = Object.values(PROVIDER_KEY_FIELDS);

const aiSettings = {
  selectedModel: "Gemini:gemini-3.7-flash",
  deepseekKey: "",
  geminiKey: "",
  openaiKey: "",
  glmKey: "",
  qwenKey: "",
  ollamaModel: "",
  ollamaBaseUrl: "",
  embeddingProvider: "Ollama",
  embeddingModel: "qwen3-embedding:0.6b",
  embeddingDimensions: 768,
  embeddingBaseUrl: "http://localhost:11434",
};

let currentActiveProvider = null;
let resumeWorkspace;
const providerDraft = {};
let keyVisible = false;
let settingsSaving = false;
let settingsSaveFeedback;

function secureDesktopBridge() {
  return window.weCanFindInternDesktop || null;
}

function secretValues(settings) {
  return Object.fromEntries(SECRET_FIELDS.map((field) => [field, settings[field] || ""]));
}

function settingsWithoutSecrets(settings = aiSettings) {
  const result = { ...settings };
  SECRET_FIELDS.forEach((field) => { delete result[field]; });
  return result;
}

function setKeyVisibility(visible) {
  const input = $("#setting-api-key");
  const btn = $("#toggle-key-visibility");
  if (!input || !btn) return;
  keyVisible = Boolean(visible);
  input.type = keyVisible ? "text" : "password";
  btn.setAttribute("aria-pressed", String(keyVisible));
  btn.setAttribute("aria-label", keyVisible ? "Hide API key" : "Show API key");
  btn.title = keyVisible ? "Hide API key" : "Show API key";
}

function splitModelValue(value) {
  const idx = value.indexOf(":");
  return idx === -1 ? [value, ""] : [value.slice(0, idx), value.slice(idx + 1)];
}

function setSettingsGroups(showOllamaConfig) {
  const keyGroup = $("#setting-api-key-group");
  const ollamaGroup = $("#ollama-config-group");
  if (keyGroup) keyGroup.style.display = showOllamaConfig ? "none" : "";
  if (ollamaGroup) ollamaGroup.style.display = showOllamaConfig ? "" : "none";
}

// Keep the visible glass controls in sync when loading saved settings or provider defaults.
function fillSetting(input, value) {
  if (!input) return;
  input.value = value;
  clearFieldError(input);
  input.dispatchEvent(new Event("settings:fill"));
}

function syncKeyFieldWithModel() {
  const modelVal = $("#setting-selected-model")?.value || "";
  const [newProvider] = splitModelValue(modelVal);
  const keyInput = $("#setting-api-key");
  const keyLabel = $("#setting-key-label");

  if (!keyInput || !keyLabel) return;

  const previousKeyField = PROVIDER_KEY_FIELDS[currentActiveProvider];
  if (previousKeyField) providerDraft[previousKeyField] = keyInput.value.trim();
  if (currentActiveProvider === "Ollama") {
    providerDraft.ollamaModel = $("#setting-ollama-model").value.trim();
    providerDraft.ollamaBaseUrl = $("#setting-ollama-base-url").value.trim();
  }

  if (newProvider !== currentActiveProvider) {
    clearFieldError(keyInput);
    clearFieldError($("#setting-ollama-model"));
    clearFieldError($("#setting-ollama-base-url"));
  }
  currentActiveProvider = newProvider;
  keyInput.disabled = false;

  if (newProvider === "DeepSeek") {
    keyLabel.textContent = "DeepSeek API Key";
    keyInput.placeholder = "Enter your DeepSeek API key (sk-...)";
    keyInput.value = providerDraft.deepseekKey || "";
  } else if (newProvider === "OpenAI") {
    keyLabel.textContent = "OpenAI API Key";
    keyInput.placeholder = "Enter your OpenAI API key (sk-...)";
    keyInput.value = providerDraft.openaiKey || "";
  } else if (newProvider === "GLM") {
    keyLabel.textContent = "GLM API Key";
    keyInput.placeholder = "Enter your GLM API key";
    keyInput.value = providerDraft.glmKey || "";
  } else if (newProvider === "Qwen") {
    keyLabel.textContent = "Qwen API Key";
    keyInput.placeholder = "Enter your Qwen API key (sk-...)";
    keyInput.value = providerDraft.qwenKey || "";
  } else if (newProvider === "Ollama") {
    keyLabel.textContent = "No API Key Needed";
    keyInput.placeholder = "Local Ollama — no API key";
    keyInput.value = "";
    keyInput.disabled = true;
    const ollamaModelInput = $("#setting-ollama-model");
    const ollamaBaseUrlInput = $("#setting-ollama-base-url");
    fillSetting(ollamaModelInput, providerDraft.ollamaModel || "");
    fillSetting(ollamaBaseUrlInput, providerDraft.ollamaBaseUrl || "");
    setSettingsGroups(true);
  } else if (newProvider === "Gemini") {
    keyLabel.textContent = "Gemini API Key";
    keyInput.placeholder = "Enter your Gemini API key (AIzaSy...)";
    keyInput.value = providerDraft.geminiKey || "";
  } else {
    keyLabel.textContent = "API Key";
    keyInput.placeholder = "Enter your API key";
    keyInput.value = "";
  }
  if (newProvider !== "Ollama") {
    setSettingsGroups(false);
  }
}

async function loadSettings() {
  setKeyVisibility(false);
  let parsed = null;
  try {
    const saved = localStorage.getItem(SETTINGS_STORAGE_KEY);
    if (saved) {
      parsed = JSON.parse(saved);
      Object.assign(aiSettings, parsed);
    }
  } catch (error) {
    showSystemFault(error, { title: "Saved AI settings could not be read", guidance: "Save your AI settings again to replace the invalid browser data." });
  }

  const desktop = secureDesktopBridge();
  if (desktop) {
    try {
      const secureValues = await desktop.getAiSecrets();
      const migratedValues = Object.fromEntries(SECRET_FIELDS.map((field) => [
        field,
        secureValues[field] || parsed?.[field] || "",
      ]));
      Object.assign(aiSettings, migratedValues);
      if (SECRET_FIELDS.some((field) => parsed?.[field])) {
        await desktop.setAiSecrets(migratedValues);
        localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settingsWithoutSecrets()));
      }
    } catch (error) {
      showSystemFault(error, {
        title: "Secure AI keys could not be read",
        guidance: "Check that the operating-system credential store is available, then restart the app.",
      });
    }
  }

  let selectedModel = aiSettings.selectedModel || "Gemini:gemini-3.7-flash";
  const [provider, legacyModel] = splitModelValue(selectedModel);
  if (provider === "Ollama") {
    if (legacyModel && !aiSettings.ollamaModel) aiSettings.ollamaModel = legacyModel;
    selectedModel = "Ollama:";
  }
  const modelEl = $("#setting-selected-model");
  fillSetting(modelEl, selectedModel);
  // Populate the controls from the saved values before treating any contents
  // as edits to a previous provider.
  currentActiveProvider = null;
  Object.assign(providerDraft, aiSettings);
  syncKeyFieldWithModel();
  const embeddingProvider = $("#setting-embedding-provider");
  const embeddingModel = $("#setting-embedding-model");
  const embeddingDimensions = $("#setting-embedding-dimensions");
  const embeddingBaseUrl = $("#setting-embedding-base-url");
  fillSetting(embeddingProvider, aiSettings.embeddingProvider || "Ollama");
  fillSetting(embeddingModel, aiSettings.embeddingModel || "qwen3-embedding:0.6b");
  fillSetting(embeddingDimensions, aiSettings.embeddingDimensions || 768);
  fillSetting(embeddingBaseUrl, aiSettings.embeddingBaseUrl || "");
  await syncEmbeddingConfig();
}

function markSetting(field, message) {
  const active = document.querySelector(".tab-pane.active");
  if (active && active.id !== "tab-settings") {
    resumeWorkspace = { id: active.id, trigger: document.activeElement };
    document.dispatchEvent(new CustomEvent("settings:navigate", { detail: "tab-settings" }));
  }
  const settings = $("#preferences-editor");
  const tab = field.closest('[role="tabpanel"]').getAttribute("aria-label") === "Embeddings" ? 1 : 0;
  settings.dispatchEvent(new CustomEvent("settings:section", { detail: tab }));
  const visibleControl = field.hidden ? field.closest(".settings-picker")?.querySelector('button[aria-haspopup="menu"]') : null;
  showFieldError(visibleControl || field, message);
}

function validSettingsForm() {
  const model = $("#setting-embedding-model");
  const dimensions = $("#setting-embedding-dimensions");
  if (!model.value.trim()) {
    markSetting(model, "Enter the embedding model name.");
    return false;
  }
  if (!Number.isInteger(dimensions.valueAsNumber) || dimensions.valueAsNumber < 1 || dimensions.valueAsNumber > 4096) {
    markSetting(dimensions, "Enter a whole number from 1 to 4096.");
    return false;
  }
  const urls = [$("#setting-embedding-base-url")];
  if ($("#setting-selected-model").value === "Ollama:") urls.unshift($("#setting-ollama-base-url"));
  for (const field of urls) {
    if (!field.value.trim()) continue;
    try {
      const { protocol } = new URL(field.value);
      if (protocol === "http:" || protocol === "https:") continue;
    } catch { /* Show the correction next to the URL. */ }
    markSetting(field, "Enter a complete http:// or https:// address.");
    return false;
  }
  return true;
}

async function saveSettings() {
  if (settingsSaving || !validSettingsForm()) return;
  const modelVal = $("#setting-selected-model")?.value || "";
  const [provider] = splitModelValue(modelVal);
  const currentKey = $("#setting-api-key")?.value.trim() || "";

  const next = { ...aiSettings, ...providerDraft };
  next.selectedModel = modelVal;
  const keyField = PROVIDER_KEY_FIELDS[provider];
  if (keyField) next[keyField] = currentKey;
  if (provider === "Ollama") {
    next.ollamaModel = $("#setting-ollama-model")?.value.trim() || "";
    next.ollamaBaseUrl = $("#setting-ollama-base-url")?.value.trim() || "";
  }
  next.embeddingProvider = $("#setting-embedding-provider")?.value || "Ollama";
  next.embeddingModel = $("#setting-embedding-model")?.value.trim() || "";
  next.embeddingDimensions = Number($("#setting-embedding-dimensions")?.value || 768);
  next.embeddingBaseUrl = $("#setting-embedding-base-url")?.value.trim() || "";

  const desktop = secureDesktopBridge();
  settingsSaving = true;
  const finish = (settingsSaveFeedback ??= createActionFeedback("#btn-save-settings", { scope: "#preferences-editor" })).begin();
  let succeeded = false;
  let savedCurrent = false;
  try {
    if (desktop) {
      await desktop.setAiSecrets(secretValues(next));
      localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settingsWithoutSecrets(next)));
    } else {
      localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(next));
    }
    Object.assign(aiSettings, next);
    if (!await syncEmbeddingConfig({ showError: true })) return;
    succeeded = true;
  } catch (error) {
    showToast(error, { title: "AI settings could not be saved", variant: "error" });
    return;
  } finally {
    settingsSaving = false;
    savedCurrent = finish(succeeded);
  }

  if (savedCurrent && resumeWorkspace && !$("#tab-settings").hidden) {
    const { id, trigger } = resumeWorkspace;
    document.dispatchEvent(new CustomEvent("settings:navigate", { detail: id }));
    trigger?.focus({ preventScroll: true });
  }
}

function getEffectiveAiConfig() {
  if (!aiSettings.selectedModel) {
    return { provider: null, model_name: null, api_key: null };
  }
  const [provider, modelNameFromValue] = splitModelValue(aiSettings.selectedModel);
  let modelName = modelNameFromValue;
  const keyField = PROVIDER_KEY_FIELDS[provider];
  const apiKey = keyField ? aiSettings[keyField] || null : null;
  let apiBase = null;
  if (provider === "Ollama") {
    modelName = aiSettings.ollamaModel || "";
    apiBase = aiSettings.ollamaBaseUrl || null;
  }
  const embeddingProvider = aiSettings.embeddingProvider || "Ollama";
  const embeddingKeys = {
    OpenAI: aiSettings.openaiKey || null,
    Gemini: aiSettings.geminiKey || null,
    Ollama: null,
  };
  return {
    provider: provider || null,
    model_name: modelName || null,
    api_key: apiKey,
    api_base: apiBase,
    embedding_provider: embeddingProvider,
    embedding_model: aiSettings.embeddingModel || null,
    embedding_dimensions: Number(aiSettings.embeddingDimensions || 768),
    embedding_api_key: embeddingKeys[embeddingProvider] || null,
    embedding_api_base: aiSettings.embeddingBaseUrl || null,
  };
}

async function syncEmbeddingConfig({ showError = false } = {}) {
  const config = getEffectiveAiConfig();
  if (!config.embedding_provider || !config.embedding_model) {
    if (showError) markSetting($("#setting-embedding-model"), "Enter the embedding model name.");
    return false;
  }
  try {
    const response = await fetch("/api/v1/agent/embedding-config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: config.embedding_provider,
        model: config.embedding_model,
        dimensions: config.embedding_dimensions,
        api_key: config.embedding_api_key,
        api_base: config.embedding_api_base || "",
      }),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || "Embedding configuration could not be applied.");
    }
    return true;
  } catch (error) {
    if (showError) {
      showToast(error, { title: "Embedding settings could not be applied", variant: "error" });
    } else {
      console.warn("Embedding settings could not be applied:", error);
    }
    return false;
  }
}

function validateAiConfig() {
  const config = getEffectiveAiConfig();
  let field, message;
  if (!config.provider || (!config.model_name && config.provider !== "Ollama")) {
    field = "#setting-selected-model";
    message = "Choose an AI model.";
  } else if (!config.model_name) {
    field = "#setting-ollama-model";
    message = "Enter the name of your local Ollama model.";
  } else if (!config.api_key && config.provider !== "Ollama") {
    field = "#setting-api-key";
    message = "Enter your " + config.provider + " API key.";
  }
  if (field) throw Object.assign(new Error(message), { field });
  return config;
}

function getAiConfigOrShowError() {
  try {
    return validateAiConfig();
  } catch (error) {
    const selection = $("#setting-selected-model");
    const savedModel = aiSettings.selectedModel?.startsWith("Ollama:") ? "Ollama:" : aiSettings.selectedModel || "";
    if (selection.value !== savedModel) {
      fillSetting(selection, savedModel);
      syncKeyFieldWithModel();
    }
    markSetting($(error.field), error.message);
    return null;
  }
}

$("#tab-settings")?.addEventListener("settings:leave", () => {
  resumeWorkspace = null;
  setKeyVisibility(false);
  $("#preferences-editor").querySelectorAll('[aria-invalid="true"]').forEach(clearFieldError);
});
$("#btn-save-settings")?.addEventListener("click", () => { void saveSettings(); });

$("#setting-selected-model")?.addEventListener("change", () => {
  syncKeyFieldWithModel();
});

$("#setting-embedding-provider")?.addEventListener("change", (e) => {
  const defaults = {
    Ollama: { model: "qwen3-embedding:0.6b", base: "http://localhost:11434" },
    Gemini: { model: "gemini-embedding-001", base: "" },
    OpenAI: { model: "text-embedding-3-small", base: "" },
  };
  const selected = defaults[e.target.value];
  if (!selected) return;
  const modelInput = $("#setting-embedding-model");
  const baseInput = $("#setting-embedding-base-url");
  fillSetting(modelInput, selected.model);
  fillSetting(baseInput, selected.base);
});

$("#toggle-key-visibility")?.addEventListener("click", () => setKeyVisibility(!keyVisible));

export {
  getAiConfigOrShowError,
  validateAiConfig,
  loadSettings,
};
