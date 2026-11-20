import { GlassSegmented, GlassSwitch } from "@zakisheriff/liquid-glass";
import { useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { Icon } from "../../layouts/AppChrome";

const views = [
  { label: "List", icon: <Icon name="list" /> },
  { label: "Map", icon: <Icon name="map" /> },
];

function subscribeToView(notify: () => void) {
  document.addEventListener("wcfi:public-view-changed", notify);
  return () => document.removeEventListener("wcfi:public-view-changed", notify);
}

export function PublicViewControl() {
  const selected = useSyncExternalStore(subscribeToView,
    () => document.getElementById("tab-heatmap")?.hidden === false ? 1 : 0, () => 0);
  return (
    <GlassSegmented className="posting-view-segments" aria-label="Public jobs view"
      size="sm" options={views} value={selected} onChange={index =>
        document.dispatchEvent(new CustomEvent("wcfi:request-public-view", { detail: index === 1 ? "map" : "list" }))
      }
    />
  );
}

const syncChoices = {
  jobs: ["Job postings", "Import postings from the included WaterlooWorks boards."],
  submitted: ["Submitted applications", "Update your application records, statuses, and deadlines."],
} as const;

type WaterlooSyncOption = keyof typeof syncChoices;

export function WaterlooSyncChoice({ option }: { option: WaterlooSyncOption }) {
  const [checked, setChecked] = useState(true);
  const ref = useRef<HTMLButtonElement>(null);
  const id = "ww-sync-" + option;
  const [label, description] = syncChoices[option];
  useLayoutEffect(() => {
    ref.current?.dispatchEvent(new Event("change", { bubbles: true }));
  }, [checked]);
  return (
    <div className="ww-sync-choice-heading" data-field-control>
      <label htmlFor={id}>
        <strong id={id + "-label"}>{label}</strong>
        <small id={id + "-description"}>{description}</small>
      </label>
      <GlassSwitch ref={ref} id={id} checked={checked} onChange={setChecked} tint="var(--accent)"
        aria-labelledby={id + "-label"} aria-describedby={id + "-description"} />
    </div>
  );
}
