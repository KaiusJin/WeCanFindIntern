import { $, showErrorDialog } from "./helpers.js?v=20260901-error-dialog-minimal-v1";

// =========================================================
// GLOBAL AI SETTINGS & LOCALSTORAGE
// =========================================================

const SETTINGS_STORAGE_KEY = "wecanfindintern_ai_settings_v3";
const SECRET_FIELDS = ["deepseekKey", "geminiKey", "openaiKey", "glmKey", "qwenKey"];

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

let currentActiveProvider = "Gemini";
let keyVisible = false;

function secureDesktopBridge() {
  return window.weCanFindInternDesktop || null;
}

function secretValues() {
  return Object.fromEntries(SECRET_FIELDS.map((field) => [field, aiSettings[field] || ""]));
}

function settingsWithoutSecrets() {
  const result = { ...aiSettings };
  SECRET_FIELDS.forEach((field) => { delete result[field]; });
  return result;
}

const EYE_SVG_OPEN = `<svg class="eye-svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>`;
const EYE_SVG_OFF = `<svg class="eye-svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>`;

function setKeyVisibility(visible) {
  const input = $("#setting-api-key");
  const btn = $("#toggle-key-visibility");
  if (!input || !btn) return;
  keyVisible = Boolean(visible);
  input.type = keyVisible ? "text" : "password";
  btn.innerHTML = keyVisible ? EYE_SVG_OFF : EYE_SVG_OPEN;
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

function syncKeyFieldWithModel() {
  const modelVal = $("#setting-selected-model")?.value || "";
  const [newProvider] = splitModelValue(modelVal);
  const keyInput = $("#setting-api-key");
  const keyLabel = $("#setting-key-label");

  if (!keyInput || !keyLabel) return;

  if (currentActiveProvider && keyInput.value.trim()) {
    if (currentActiveProvider === "DeepSeek") aiSettings.deepseekKey = keyInput.value.trim();
    else if (currentActiveProvider === "OpenAI") aiSettings.openaiKey = keyInput.value.trim();
    else if (currentActiveProvider === "Gemini") aiSettings.geminiKey = keyInput.value.trim();
    else if (currentActiveProvider === "GLM") aiSettings.glmKey = keyInput.value.trim();
    else if (currentActiveProvider === "Qwen") aiSettings.qwenKey = keyInput.value.trim();
  }
  if (currentActiveProvider === "Ollama") {
    aiSettings.ollamaModel = $("#setting-ollama-model")?.value.trim() || "";
    aiSettings.ollamaBaseUrl = $("#setting-ollama-base-url")?.value.trim() || "";
  }

  currentActiveProvider = newProvider;
  keyInput.disabled = false;

  if (newProvider === "DeepSeek") {
    keyLabel.textContent = "DeepSeek API Key";
    keyInput.placeholder = "Enter your DeepSeek API key (sk-...)";
    keyInput.value = aiSettings.deepseekKey || "";
  } else if (newProvider === "OpenAI") {
    keyLabel.textContent = "OpenAI API Key";
    keyInput.placeholder = "Enter your OpenAI API key (sk-...)";
    keyInput.value = aiSettings.openaiKey || "";
  } else if (newProvider === "GLM") {
    keyLabel.textContent = "GLM API Key";
    keyInput.placeholder = "Enter your GLM API key";
    keyInput.value = aiSettings.glmKey || "";
  } else if (newProvider === "Qwen") {
    keyLabel.textContent = "Qwen API Key";
    keyInput.placeholder = "Enter your Qwen API key (sk-...)";
    keyInput.value = aiSettings.qwenKey || "";
  } else if (newProvider === "Ollama") {
    keyLabel.textContent = "No API Key Needed";
    keyInput.placeholder = "Local Ollama — no API key";
    keyInput.value = "";
    keyInput.disabled = true;
    const ollamaModelInput = $("#setting-ollama-model");
    const ollamaBaseUrlInput = $("#setting-ollama-base-url");
    if (ollamaModelInput) ollamaModelInput.value = aiSettings.ollamaModel || "";
    if (ollamaBaseUrlInput) ollamaBaseUrlInput.value = aiSettings.ollamaBaseUrl || "";
    setSettingsGroups(true);
  } else if (newProvider === "Gemini") {
    keyLabel.textContent = "Gemini API Key";
    keyInput.placeholder = "Enter your Gemini API key (AIzaSy...)";
    keyInput.value = aiSettings.geminiKey || "";
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
    showErrorDialog(error, { title: "Saved AI settings could not be read", guidance: "Save your AI settings again to replace the invalid browser data." });
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
      showErrorDialog(error, {
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
  if (modelEl) modelEl.value = selectedModel;
  currentActiveProvider = provider;
  syncKeyFieldWithModel();
  const embeddingProvider = $("#setting-embedding-provider");
  const embeddingModel = $("#setting-embedding-model");
  const embeddingDimensions = $("#setting-embedding-dimensions");
  const embeddingBaseUrl = $("#setting-embedding-base-url");
  if (embeddingProvider) embeddingProvider.value = aiSettings.embeddingProvider || "Ollama";
  if (embeddingModel) embeddingModel.value = aiSettings.embeddingModel || "qwen3-embedding:0.6b";
  if (embeddingDimensions) embeddingDimensions.value = aiSettings.embeddingDimensions || 768;
  if (embeddingBaseUrl) embeddingBaseUrl.value = aiSettings.embeddingBaseUrl || "";
}

async function saveSettings() {
  const modelVal = $("#setting-selected-model")?.value || "";
  const [provider] = splitModelValue(modelVal);
  const currentKey = $("#setting-api-key")?.value.trim() || "";

  aiSettings.selectedModel = modelVal;
  if (provider === "DeepSeek") {
    aiSettings.deepseekKey = currentKey;
  } else if (provider === "OpenAI") {
    aiSettings.openaiKey = currentKey;
  } else if (provider === "GLM") {
    aiSettings.glmKey = currentKey;
  } else if (provider === "Qwen") {
    aiSettings.qwenKey = currentKey;
  } else if (provider === "Ollama") {
    aiSettings.ollamaModel = $("#setting-ollama-model")?.value.trim() || "";
    aiSettings.ollamaBaseUrl = $("#setting-ollama-base-url")?.value.trim() || "";
  } else if (provider === "Gemini") {
    aiSettings.geminiKey = currentKey;
  }
  aiSettings.embeddingProvider = $("#setting-embedding-provider")?.value || "Ollama";
  aiSettings.embeddingModel = $("#setting-embedding-model")?.value.trim() || "";
  aiSettings.embeddingDimensions = Number($("#setting-embedding-dimensions")?.value || 768);
  aiSettings.embeddingBaseUrl = $("#setting-embedding-base-url")?.value.trim() || "";

  const desktop = secureDesktopBridge();
  try {
    if (desktop) {
      await desktop.setAiSecrets(secretValues());
      localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settingsWithoutSecrets()));
    } else {
      localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(aiSettings));
    }
  } catch (error) {
    showErrorDialog(error, { title: "AI settings could not be saved", guidance: "Allow local storage and make sure the operating-system credential store is available." });
    return;
  }

  const feedback = $("#settings-save-feedback");
  if (feedback) {
    feedback.textContent = desktop
      ? "✓ Saved in the operating-system secure store!"
      : "✓ Saved in this browser!";
    feedback.hidden = false;
    setTimeout(() => { feedback.hidden = true; }, 2500);
  }
  setTimeout(() => { $("#settings-dialog")?.close(); }, 500);
}

function getEffectiveAiConfig() {
  if (!aiSettings.selectedModel) {
    return { provider: null, model_name: null, api_key: null };
  }
  const [provider, modelNameFromValue] = splitModelValue(aiSettings.selectedModel);
  let modelName = modelNameFromValue;
  let apiKey = null;
  let apiBase = null;
  if (provider === "DeepSeek") {
    apiKey = aiSettings.deepseekKey || null;
  } else if (provider === "OpenAI") {
    apiKey = aiSettings.openaiKey || null;
  } else if (provider === "GLM") {
    apiKey = aiSettings.glmKey || null;
  } else if (provider === "Qwen") {
    apiKey = aiSettings.qwenKey || null;
  } else if (provider === "Ollama") {
    modelName = aiSettings.ollamaModel || "";
    apiBase = aiSettings.ollamaBaseUrl || null;
  } else if (provider === "Gemini") {
    apiKey = aiSettings.geminiKey || null;
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

function validateAiConfig() {
  const config = getEffectiveAiConfig();
  if (!config.provider) {
    throw new Error("Please open Settings (⚙) and select an AI model first.");
  }
  if (!config.model_name) {
    const message = config.provider === "Ollama"
      ? "Please enter your local Ollama model name in Settings (⚙)."
      : "Please open Settings (⚙) and select an AI model first.";
    throw new Error(message);
  }
  if (!config.api_key && config.provider !== "Ollama") {
    throw new Error(`Missing ${config.provider} API key. Please open Settings (⚙) and enter your API key.`);
  }
  return config;
}

function syncDialogScrollLock() {
  const anyOpen = Array.from(document.querySelectorAll("dialog")).some((d) => d.open);
  if (anyOpen) {
    document.body.classList.add("modal-open");
    document.documentElement.classList.add("modal-open");
  } else {
    document.body.classList.remove("modal-open");
    document.documentElement.classList.remove("modal-open");
  }
}

document.querySelectorAll("dialog").forEach((dlg) => {
  dlg.addEventListener("close", syncDialogScrollLock);
  dlg.addEventListener("cancel", syncDialogScrollLock);
});

async function openSettingsDialog() {
  await loadSettings();
  const dialog = $("#settings-dialog");
  dialog?.showModal();
  syncDialogScrollLock();
}

$("#open-settings")?.addEventListener("click", openSettingsDialog);
$("#sidebar-open-settings")?.addEventListener("click", openSettingsDialog);
$("#close-settings")?.addEventListener("click", () => $("#settings-dialog")?.close());
$("#btn-save-settings")?.addEventListener("click", () => { saveSettings(); });
$("#settings-dialog")?.addEventListener("click", (e) => {
  if (e.target === $("#settings-dialog")) $("#settings-dialog")?.close();
});

$("#setting-selected-model")?.addEventListener("change", (e) => {
  syncKeyFieldWithModel();
});

$("#setting-api-key")?.addEventListener("input", (e) => {
  const modelVal = $("#setting-selected-model")?.value || "";
  const [provider] = splitModelValue(modelVal);
  const val = e.target.value.trim();
  if (provider === "DeepSeek") aiSettings.deepseekKey = val;
  else if (provider === "OpenAI") aiSettings.openaiKey = val;
  else if (provider === "Gemini") aiSettings.geminiKey = val;
  else if (provider === "GLM") aiSettings.glmKey = val;
  else if (provider === "Qwen") aiSettings.qwenKey = val;
});

$("#setting-ollama-model")?.addEventListener("input", (e) => {
  aiSettings.ollamaModel = e.target.value.trim();
});

$("#setting-ollama-base-url")?.addEventListener("input", (e) => {
  aiSettings.ollamaBaseUrl = e.target.value.trim();
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
  if (modelInput) modelInput.value = selected.model;
  if (baseInput) baseInput.value = selected.base;
});

$("#toggle-key-visibility")?.addEventListener("click", () => {
  const input = $("#setting-api-key");
  const btn = $("#toggle-key-visibility");
  if (!input || !btn) return;
  setKeyVisibility(!keyVisible);
});

export {
  aiSettings,
  currentActiveProvider,
  getEffectiveAiConfig,
  validateAiConfig,
  syncDialogScrollLock,
  loadSettings,
  saveSettings,
};
