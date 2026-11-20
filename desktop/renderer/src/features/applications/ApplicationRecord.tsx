import { GlassButton, GlassCard, GlassInput, GlassMenu, GlassSegmented } from "@zakisheriff/liquid-glass";
import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { handleGlassMenuKey, focusGlassMenuTrigger } from "../../components/glass-menu-keyboard";
import { Icon } from "../../layouts/AppChrome";

type Mode = "drawer" | "custom";
const sections = [
  { label: "Details", icon: <Icon name="pen" /> },
  { label: "Activity", icon: <Icon name="history" /> },
];
type Choice = { value: string; label: string };
const stages = [{ value: "interested", label: "Interested" }];
const sources = [{ value: "other", label: "Other" }];
const workModes = [
  { value: "hybrid", label: "Hybrid" },
  { value: "remote", label: "Remote" },
  { value: "onsite", label: "In-person" },
];
const allWorkModes = [{ value: "", label: "Not specified" }, ...workModes, { value: "unknown", label: "Unknown" }];

export function ApplicationChoice({ id, label, choices, initial = choices[0].value, stage = false, compact = false }: {
  id: string; label: string; choices: Choice[]; initial?: string; stage?: boolean; compact?: boolean;
}) {
  const select = useRef<HTMLSelectElement>(null);
  const [options, setOptions] = useState(choices);
  const [value, setValue] = useState(initial);
  const [locked, setLocked] = useState(false);
  useLayoutEffect(() => {
    const field = select.current!;
    const host = field.closest("#tracker-detail-drawer, #custom-job-form");
    function read() {
      setOptions(Array.from(field.options, option => ({ value: option.value, label: option.text })));
      setValue(field.value);
      setLocked(field.disabled);
    }
    field.addEventListener("application:fill", read);
    field.addEventListener("change", read);
    host?.addEventListener("application:fill", read);
    host?.addEventListener("application:reset", read);
    return () => {
      field.removeEventListener("application:fill", read);
      field.removeEventListener("change", read);
      host?.removeEventListener("application:fill", read);
      host?.removeEventListener("application:reset", read);
    };
  }, []);
  useLayoutEffect(() => {
    const host = select.current!.parentElement!;
    let surface: HTMLElement | null = null;
    function place() {
      if (!surface) return;
      const anchor = host.querySelector<HTMLButtonElement>('button[aria-haspopup="menu"]')!;
      const rect = anchor.getBoundingClientRect();
      const left = Math.max(8, Math.min(rect.left, innerWidth - surface.offsetWidth - 8));
      const top = rect.top + surface.offsetHeight <= innerHeight - 8
        ? rect.top : Math.max(8, rect.bottom - surface.offsetHeight);
      surface.style.left = left + "px";
      surface.style.top = top + "px";
    }
    function stopPositioning() {
      window.removeEventListener("resize", place);
      document.removeEventListener("scroll", place, true);
    }
    // The ledger and editor scroll independently. Keep the library's live menu
    // in the top layer so the scrollports and the add form cannot cut it off.
    const observer = new MutationObserver(() => {
      const next = host.querySelector<HTMLElement>(".lg-menu-surface");
      if (next === surface) return;
      stopPositioning();
      surface = next;
      if (!surface) return;
      surface.popover = "manual";
      surface.showPopover();
      place();
      window.addEventListener("resize", place);
      document.addEventListener("scroll", place, true);
    });
    observer.observe(host, { childList: true, subtree: true });
    return () => { observer.disconnect(); stopPositioning(); };
  }, []);
  const selected = options.find(option => option.value === value)?.label || value;
  return (
    <div className={"record-field record-choice-field" + (compact ? " record-choice-compact" : "")}
      data-stage={stage ? value : undefined} onKeyDown={handleGlassMenuKey}>
      {!compact && <span className="field-label">{label}</span>}
      <select id={id} hidden ref={select} defaultValue={initial} aria-label={label}>
        {choices.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
      {locked ? <GlassButton className="record-locked-choice" size="sm" disabled aria-label={label + ", " + selected}>{selected}</GlassButton>
        : <GlassMenu className="record-choice-menu" size="sm" width={176} align="start"
          aria-label={`Change ${label.toLowerCase()}, currently ${selected}`}
          icon={<>{stage && <i aria-hidden="true" />}{selected}<Icon name="chevron-down" /></>}
          items={options.filter(option => option.value !== "" || id !== "tracker-bulk-stage").map(option => ({
            label: option.label, checked: option.value === value, onSelect: () => {
              const field = select.current!;
              field.value = option.value;
              field.dispatchEvent(new Event("change", { bubbles: true }));
              focusGlassMenuTrigger(field.parentElement);
            },
          }))} />}
    </div>
  );
}

function RecordText({ id, label, placeholder, required = false, autoFocus = false, wide = false, labelledOutside = false, type = "text" }: {
  id: string; label: string; placeholder?: string; required?: boolean; autoFocus?: boolean; wide?: boolean;
  labelledOutside?: boolean; type?: "text" | "url";
}) {
  const ref = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState("");
  const [locked, setLocked] = useState(false);
  useLayoutEffect(() => {
    const field = ref.current!;
    const host = field.closest("form, aside")!;
    function read() { setValue(field.value); setLocked(field.disabled); }
    function reset() { setValue(""); }
    host.addEventListener("application:fill", read);
    host.addEventListener("application:reset", reset);
    return () => {
      host.removeEventListener("application:fill", read);
      host.removeEventListener("application:reset", reset);
    };
  }, []);
  return (
    <div className={"record-field" + (wide ? " record-wide" : "")}>
      <GlassInput ref={ref} id={id} type={type} label={labelledOutside ? undefined : label} aria-label={label}
        className="record-glass-input" required={required} autoFocus={autoFocus} autoComplete="off"
        placeholder={placeholder} disabled={locked} value={value} onChange={event => setValue(event.target.value)} />
    </div>
  );
}

function Field({ id, label, type = "text", children, wide = false }: {
  id: string; label: string; type?: string; children?: ReactNode; wide?: boolean;
}) {
  return (
    <div className={"record-field" + (wide ? " record-wide" : "")}>
      <label className="field-label" htmlFor={id}>{label}</label>
      {children || <input className="career-input" id={id} type={type} />}
    </div>
  );
}

function ProgressFields({ mode }: { mode: Mode }) {
  return (
    <GlassCard className="record-progress" role="group" aria-label="Application progress" radius="16px" padding="15px 16px" variant="clear">
      <h3>Application progress</h3>
      <div className="record-field-grid">
        <ApplicationChoice id={mode + "-stage"} label="Stage" choices={stages} stage />
        <Field id={mode + "-applied-at"} label="Applied date" type="date" />
        {mode === "drawer" && <div id="drawer-external-status-group" className="record-wide record-external" hidden>
          <span>WaterlooWorks status</span><strong id="drawer-external-status" />
        </div>}
      </div>
    </GlassCard>
  );
}

function JobFields({ mode }: { mode: Mode }) {
  const edit = mode === "drawer";
  return (
      <section className="record-role">
        <h3>Position</h3>
        {edit && <p id="drawer-origin-notice" className="record-origin" hidden />}
        <div className="record-field-grid job-content-field">
          <RecordText id={edit ? "drawer-company-input" : "custom-company"} label="Company" required
            autoFocus={!edit} placeholder="Company name" />
          <RecordText id={edit ? "drawer-title-input" : "custom-title"} label="Job title" required placeholder="Job title or role" />
          <RecordText id={mode + "-location"} label="Location" placeholder="City, region or remote" />
          <ApplicationChoice id={mode + "-work-mode"} label="Work mode" choices={edit ? allWorkModes : workModes} />
        </div>
      </section>
  );
}

function PostingFields({ mode }: { mode: Mode }) {
  const edit = mode === "drawer";
  return (
      <details className="record-posting">
        <summary><Icon name="document" />Posting details<Icon name="chevron-down" /></summary>
        <div className="record-field-grid job-content-field">
          <ApplicationChoice id={mode + "-source"} label="Source" choices={sources} />
          <Field id={mode + "-deadline"} label="Application deadline" type="date" />
          <RecordText id={mode + "-salary"} label="Salary / pay rate" wide />
          {edit ? <div className="field-group job-content-field tracker-link-field">
            <label className="field-label" htmlFor="drawer-url">Posting link</label>
            <div className="tracker-link-row" data-field-control="">
              <RecordText id="drawer-url" label="Posting link" type="url" placeholder="https://" labelledOutside />
              <GlassButton id="drawer-copy-job" size="sm">Copy</GlassButton>
              <GlassButton id="drawer-open-job" size="sm" href="#" target="_blank" icon={<Icon name="arrow-up-right" />}>Open</GlassButton>
            </div>
          </div> : <RecordText id="custom-url" label="Posting link" type="url" placeholder="https://" wide />}
          <Field id={edit ? "drawer-jd-input" : "custom-job-description"} label="Job description" wide>
            {edit && <div id="drawer-jd-readonly" className="tracker-jd-copy" hidden />}
            <textarea id={edit ? "drawer-jd-input" : "custom-job-description"} className="career-textarea"
              rows={4} placeholder="Paste the job description…" />
          </Field>
        </div>
      </details>
  );
}

export function ApplicationActions() {
  return <>
    <GlassButton id="tracker-export" size="sm" href="/api/v1/tracker/export.csv" icon={<Icon name="document" />}>Export CSV</GlassButton>
    <GlassButton id="btn-open-custom-job" popoverTarget="custom-job-dialog" size="sm"
      variant="tinted-filled" tint="var(--accent)" icon={<Icon name="plus" />}>Add application</GlassButton>
  </>;
}

export function ApplicationRecord() {
  const [active, setActive] = useState(0);
  const body = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const host = body.current!.closest("aside")!;
    const showDetails = () => setActive(0);
    host.addEventListener("application:details", showDetails);
    return () => host.removeEventListener("application:details", showDetails);
  }, []);
  return (
    <>
      <header className="record-heading">
        <div className="record-heading-text">
          <h2 id="drawer-title">Application</h2>
          <p><span id="drawer-company" /><span id="drawer-location-label" /></p>
        </div>
        <GlassButton id="close-tracker-drawer" size="sm" icon={<Icon name="close" />} aria-label="Close application details" />
      </header>
      <div className="record-toolbar">
        <GlassSegmented className="record-sections" size="sm" aria-label="Application sections" options={sections}
          value={active} onChange={index => { setActive(index); body.current!.scrollTop = 0; }} />
      </div>
      <div className="tracker-drawer-body record-body" ref={body}>
        <input id="drawer-app-id" type="hidden" />
        <div role="tabpanel" aria-label="Details" hidden={active !== 0}>
          <ProgressFields mode="drawer" />
          <JobFields mode="drawer" />
          <PostingFields mode="drawer" />
        </div>
        <section className="record-activity" role="tabpanel" aria-label="Activity" hidden={active !== 1}>
          <h3>Activity history</h3>
          <div id="tracker-timeline" className="tracker-timeline" />
        </section>
      </div>
      <footer className="record-footer" hidden={active !== 0}>
        <GlassButton id="btn-delete-tracked-app" size="sm" variant="clear" tint="var(--danger)" destructive>Delete application</GlassButton>
        <GlassButton id="save-tracker-drawer" size="sm" variant="tinted-filled" tint="var(--accent)"
          icon={<Icon name="check" />}>Save changes</GlassButton>
      </footer>
    </>
  );
}

export function ApplicationForm() {
  return (
    <>
      <header className="record-heading">
        <h2 id="custom-job-heading">Add application</h2>
        <GlassButton id="close-custom-job-dialog" size="sm" icon={<Icon name="close" />} aria-label="Close add application"
          popoverTarget="custom-job-dialog" popoverTargetAction="hide" />
      </header>
      <form id="custom-job-form" noValidate>
        <div className="record-form-body">
          <JobFields mode="custom" />
          <ProgressFields mode="custom" />
          <PostingFields mode="custom" />
        </div>
        <footer className="record-footer">
          <GlassButton id="btn-cancel-custom-job" popoverTarget="custom-job-dialog" popoverTargetAction="hide" size="sm">Cancel</GlassButton>
          <GlassButton type="submit" size="sm" variant="tinted-filled" tint="var(--accent)" icon={<Icon name="check" />}>Save application</GlassButton>
        </footer>
      </form>
    </>
  );
}
