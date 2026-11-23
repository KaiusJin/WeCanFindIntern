import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { flushSync } from "react-dom";
import { GlassButton, GlassInput, GlassNav, GlassSlider } from "@zakisheriff/liquid-glass";
import { handleGlassMenuKey } from "../components/glass-menu-keyboard";
import { SelectMenu } from "../components/SelectMenu";
import { AppearanceControl, Icon } from "../layouts/AppChrome";

const tabs = [
  { label: "AI model", icon: <Icon name="sparkles" /> },
  { label: "Embeddings", icon: <Icon name="layers" /> },
];

function SettingsMenu({ id, label, initial, children }: { id: string; label: string; initial: string; children: ReactNode }) {
  return <div className="settings-picker" data-field-control="" onKeyDown={handleGlassMenuKey}>
    <span className="field-label">{label}</span>
    <SelectMenu label={label} initial={initial} syncOn={["settings:fill"]} width={268} selectProps={{ id }}>{children}</SelectMenu>
  </div>;
}

function useSetting(initial: string) {
  const field = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState(initial);
  useLayoutEffect(() => {
    const input = field.current!;
    const refresh = () => setValue(input.value);
    input.addEventListener("settings:fill", refresh);
    return () => input.removeEventListener("settings:fill", refresh);
  }, []);
  return { field, value, setValue };
}

function SettingsField({ id, initial = "", placeholder }: { id: string; initial?: string; placeholder?: string }) {
  const { field, value, setValue } = useSetting(initial);
  return (
    <GlassInput
      ref={field}
      id={id}
      value={value}
      placeholder={placeholder}
      autoComplete="off"
      onChange={(event) => setValue(event.currentTarget.value)}
    />
  );
}

function Dimensions() {
  const { field, value, setValue } = useSetting("768");
  return (
    <div className="settings-dimensions" data-field-control="">
      <label className="field-label" htmlFor="setting-embedding-dimensions">
        Dimensions
      </label>
      <input
        ref={field}
        type="number"
        id="setting-embedding-dimensions"
        min="1"
        max="4096"
        step="1"
        value={value}
        onChange={(event) => setValue(event.currentTarget.value)}
      />
      <GlassSlider
        min={128}
        max={4096}
        step={128}
        value={Math.min(4096, Math.max(128, Number(value)))}
        tint="var(--accent)"
        aria-label="Embedding dimensions"
        onChange={(next) => {
          setValue(String(next));
          field.current?.dispatchEvent(new Event("change", { bubbles: true }));
        }}
      />
    </div>
  );
}

export function SettingsContents() {
  const [tab, setTab] = useState(0);
  useLayoutEffect(() => {
    const settings = document.getElementById("preferences-editor")!;
    const reveal = (event: Event) => flushSync(() => setTab((event as CustomEvent<number>).detail));
    settings.addEventListener("settings:section", reveal);
    return () => settings.removeEventListener("settings:section", reveal);
  }, []);
  return (
    <>
      <GlassNav
        className="preferences-tabs"
        aria-label="Settings sections"
        activeIndex={tab}
        onChange={setTab}
        items={tabs}
      />
      <div className="preferences-pane" role="tabpanel" aria-label="AI model" hidden={tab !== 0}>
        <SettingsMenu id="setting-selected-model" label="AI model" initial="Gemini:gemini-3.7-flash">
          <optgroup label="Google Gemini">
            <option value="Gemini:gemini-3.7-flash">Gemini 3.7 Flash</option>
            <option value="Gemini:gemini-3.6-flash">Gemini 3.6 Flash</option>
            <option value="Gemini:gemini-3.5-flash">Gemini 3.5 Flash</option>
            <option value="Gemini:gemini-3.5-flash-lite">Gemini 3.5 Flash Lite</option>
          </optgroup>
          <optgroup label="DeepSeek">
            <option value="DeepSeek:deepseek-v4-pro">DeepSeek V4 Pro</option>
            <option value="DeepSeek:deepseek-v4-flash">DeepSeek V4 Flash</option>
          </optgroup>
          <optgroup label="Qwen">
            <option value="Qwen:qwen3-max">Qwen3 Max</option>
            <option value="Qwen:qwen3-plus">Qwen3 Plus</option>
            <option value="Qwen:qwen3-flash">Qwen3 Flash</option>
          </optgroup>
          <optgroup label="GLM">
            <option value="GLM:glm-5.3-flash">GLM 5.3 Flash</option>
            <option value="GLM:glm-5.3">GLM 5.3</option>
            <option value="GLM:glm-5.2">GLM 5.2</option>
          </optgroup>
          <optgroup label="ChatGPT">
            <option value="OpenAI:gpt-5.6-sol">GPT 5.6 Sol</option>
            <option value="OpenAI:gpt-5.6-terra">GPT 5.6 Terra</option>
            <option value="OpenAI:gpt-5.6-luna">GPT 5.6 Luna</option>
            <option value="OpenAI:gpt-5.5">GPT 5.5</option>
            <option value="OpenAI:gpt-5.4-mini">GPT 5.4 Mini</option>
          </optgroup>
          <optgroup label="Ollama (Local)">
            <option value="Ollama:">Ollama (Local) — custom</option>
          </optgroup>
        </SettingsMenu>
        <div id="setting-api-key-group">
          <label className="field-label" htmlFor="setting-api-key" id="setting-key-label">
            Gemini API Key
          </label>
          <GlassInput className="settings-key-field" type="password" id="setting-api-key" placeholder="Enter your Gemini API key"
            autoComplete="off" trailing={
              <GlassButton type="button" id="toggle-key-visibility" size="sm" variant="clear" title="Show API key"
                aria-label="Show API key" aria-pressed="false" icon={<><Icon name="eye" /><Icon name="eye-off" /></>} />
            } />
        </div>
        <div id="ollama-config-group" style={{ display: "none" }}>
          <label className="field-label" htmlFor="setting-ollama-model">
            Ollama model
          </label>
          <SettingsField id="setting-ollama-model" placeholder="e.g. llama3.2:3b" />
          <p className="field-hint">Use the name of a model you’ve pulled locally.</p>
          <label className="field-label" htmlFor="setting-ollama-base-url">
            Ollama base URL
          </label>
          <SettingsField id="setting-ollama-base-url" placeholder="http://localhost:11434/v1" />
        </div>
      </div>
      <div className="preferences-pane" role="tabpanel" aria-label="Embeddings" hidden={tab !== 1}>
        <SettingsMenu id="setting-embedding-provider" label="Embedding provider" initial="Ollama">
          <option value="Ollama">Ollama (Local)</option>
          <option value="Gemini">Google Gemini</option>
          <option value="OpenAI">OpenAI</option>
        </SettingsMenu>
        <label className="field-label" htmlFor="setting-embedding-model">
          Embedding model
        </label>
        <SettingsField id="setting-embedding-model" initial="qwen3-embedding:0.6b" />
        <Dimensions />
        <label className="field-label" htmlFor="setting-embedding-base-url">
          Embedding base URL (optional)
        </label>
        <SettingsField id="setting-embedding-base-url" initial="http://localhost:11434" />
        <p className="field-hint">Ollama uses /api/embed. Gemini and OpenAI use the key saved for that provider.</p>
      </div>
      <footer className="preferences-footer">
        <GlassButton
          id="btn-save-settings"
          variant="tinted-filled"
          tint="var(--accent)"
          size="sm"
          icon={<Icon name="check" />}
        >
          Save settings
        </GlassButton>
      </footer>
    </>
  );
}

export function Settings() {
  return (
    <div id="tab-settings" className="tab-pane" hidden>
      <section className="preferences-workspace" aria-labelledby="settings-title">
        <header className="preferences-heading"><h1 id="settings-title">Settings</h1></header>
        <div className="preferences-layout">
          <section className="appearance-preferences">
            <h2>Appearance</h2>
            <div id="settings-appearance"><AppearanceControl /></div>
          </section>
          <section className="model-preferences" aria-labelledby="model-preferences-title">
            <h2 id="model-preferences-title">AI configuration</h2>
            <div id="preferences-editor"><SettingsContents /></div>
          </section>
        </div>
      </section>
    </div>
  );
}
