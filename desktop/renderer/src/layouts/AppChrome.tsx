import { useState, type ReactNode } from "react";
import { GlassButton, GlassCard, GlassSegmented } from "@zakisheriff/liquid-glass";

const navigation = [
  {
    group: "Public Jobs",
    icon: "briefcase",
    items: [
      ["tab-jobs", "LinkedIn / Indeed", "briefcase"],
    ],
  },
  { group: "WaterlooWorks", icon: "campus", items: [["tab-waterlooworks", "WaterlooWorks", "campus"]] },
  {
    group: "Manage",
    icon: "board",
    items: [["tab-tracker", "Applications", "board"]],
  },
  {
    group: "Career tools",
    icon: "document",
    items: [
      ["tab-ats-score", "Resume ATS Score", "document"],
      ["tab-ats-match", "Job Match", "target"],
      ["tab-cover-letter", "Cover Letter", "pen"],
      ["tab-interview", "Interview Coach", "mic"],
    ],
  },
  {
    group: "AI Assistant", icon: "sparkles", items: [["tab-agent", "AI Assistant", "sparkles"]],
  },
  { group: "Profile", icon: "profile", items: [["tab-profile", "Profile", "profile"]] },
  { group: "Settings", icon: "settings", items: [["tab-settings", "Settings", "settings"]] },
] as const;

export function Icon({ name }: { name: string }) {
  return (
    <svg className="icon" aria-hidden="true" focusable="false">
      <use href={`/ui/icons.svg#${name}`} />
    </svg>
  );
}

function NavigationItems() {
  return navigation.map(({ group, icon, items }) => {
    const groupId = "nav-" + items[0][0];
    return (
      <div className={items.length === 1 ? "nav-group direct-group" : "nav-group"} key={group}>
        {items.length > 1 && (
          <GlassButton size="sm" variant="clear" className="group-toggle" type="button" aria-expanded="false" aria-controls={groupId}>
            <Icon name={icon} />
            <span>{group}</span>
            <Icon name="chevron-down" />
          </GlassButton>
        )}
        <div className="nav-items" id={groupId}>
          {items.map(([id, label, symbol]) => (
            <GlassButton size="sm" variant="clear"
              key={id}
              className={id === "tab-jobs" ? "nav-tab active" : "nav-tab"}
              data-tab={id}
              type="button"
              title={id === "tab-jobs" ? "Public Jobs · LinkedIn / Indeed" : label}
              aria-current={id === "tab-jobs" ? "page" : undefined}
            >
              <Icon name={symbol} />
              <span>{label}</span>
            </GlassButton>
          ))}
        </div>
      </div>
    );
  });
}

function WorkspacePicker() {
  return (
    <label className="workspace-picker">
      <Icon name="grid" />
      <select id="workspace-picker" aria-label="Switch workspace" defaultValue="tab-jobs">
        {navigation.map(({ group, items }) => {
          const options = items.map(([id, label]) => <option key={id} value={id}>{label}</option>);
          return items.length === 1 ? options : <optgroup key={group} label={group}>{options}</optgroup>;
        })}
      </select>
      <Icon name="chevron-down" />
    </label>
  );
}

const appearanceModes = ["system", "light", "dark"] as const;
const appearanceOptions = [
  { label: "System", icon: <Icon name="system" /> },
  { label: "Light", icon: <Icon name="sun" /> },
  { label: "Dark", icon: <Icon name="moon" /> },
];

export function AppearanceControl() {
  const [selected, setSelected] = useState(() => {
    const mode = typeof document === "undefined" ? "system" : document.documentElement.dataset.appearance;
    return Math.max(
      0,
      appearanceModes.findIndex((value) => value === mode),
    );
  });
  return (
    <GlassSegmented
      className="appearance"
      size="sm"
      options={appearanceOptions}
      value={selected}
      aria-label="Window appearance"
      onChange={(index) => {
        setSelected(index);
        document.dispatchEvent(new CustomEvent("wcfi:appearance", { detail: appearanceModes[index] }));
      }}
    />
  );
}

export function WindowToolbarControls() {
  return (
    <>
      <div className="window-brand">
        <a className="window-home" href="#public-jobs" aria-label="WeCanFindIntern home">
          <img src="/ui/mark.svg" width="27" height="27" alt="" />
          <span>WeCanFindIntern</span>
        </a>
      </div>
      <nav className="toolbar-navigator" aria-label="Main navigation">
        <GlassCard className="nav-glass-plate" aria-hidden="true" radius="22px" padding="0" />
        <div id="toolbar-nav-host">
          <div className="app-nav"><NavigationItems /></div>
        </div>
        <WorkspacePicker />
      </nav>
    </>
  );
}

export function AppChrome({ children }: { children: ReactNode }) {
  return (
    <div className="app-shell">
      <header className="window-toolbar">
        <WindowToolbarControls />
      </header>
      <div className="app-stage">
        <main id="app-main">{children}</main>
      </div>
    </div>
  );
}
