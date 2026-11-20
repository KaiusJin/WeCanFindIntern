import { GlassMenu } from "@zakisheriff/liquid-glass";
import { createRoot, type Root } from "react-dom/client";
import { focusGlassMenuTrigger, handleGlassMenuKey } from "../../components/glass-menu-keyboard";
import { Icon } from "../../layouts/AppChrome";

const toolIcons: Record<string, string> = {
  "tab-ats-match": "target",
  "tab-interview": "mic",
  "tab-cover-letter": "pen",
  "tab-agent": "sparkles",
};

// Readers are replaced by the existing DOM controller. Keep the original
// native links and buttons as the single source of URLs and job-context actions.
class PostingMenu extends HTMLElement {
  private root?: Root;

  connectedCallback() {
    const fallback = this.querySelector("details");
    const label = fallback?.querySelector("summary")?.textContent?.trim();
    if (!fallback || !label || this.root) return;
    const mount = document.createElement("div");
    const items = Array.from(fallback.querySelectorAll<HTMLElement>("button, a[href]"), action => ({
      label: action.textContent?.trim(),
      icon: <Icon name={toolIcons[action.dataset.aiTarget ?? ""] ?? "arrow-up-right"} />,
      onSelect: () => {
        focusGlassMenuTrigger(mount);
        action.click();
      },
    }));
    fallback.hidden = true;
    this.append(mount);
    this.root = createRoot(mount);
    this.root.render(
      <div onKeyDown={handleGlassMenuKey}>
        <GlassMenu align="start" size="sm" width={260} items={items} aria-label={label}
          icon={<><span>{label}</span><Icon name="chevron-down" /></>}
        />
      </div>,
    );
  }

  disconnectedCallback() {
    this.root?.unmount();
    this.root = undefined;
    this.querySelector(":scope > div")?.remove();
  }
}

customElements.define("posting-menu", PostingMenu);
