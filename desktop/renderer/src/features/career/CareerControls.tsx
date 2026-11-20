import { GlassButton, GlassSegmented } from "@zakisheriff/liquid-glass";
import { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { mountCareerFields } from "./CareerFields";
import { Icon } from "../../layouts/AppChrome";

type SourcePrefix = "cl" | "int";
const sourceOptions = [
  { label: "Profile", icon: <Icon name="profile" /> },
  { label: "Resume PDF", icon: <Icon name="document" /> },
];

function SourceSegments({ prefix }: { prefix: SourcePrefix }) {
  const [selected, setSelected] = useState(0);
  const profile = useRef<HTMLInputElement>(null);
  const pdf = useRef<HTMLInputElement>(null);
  const name = prefix + "-resume-source";
  return (
    <>
      <span hidden>
        <input ref={profile} type="radio" name={name} value="profile" defaultChecked onChange={() => setSelected(0)} />
        <input ref={pdf} type="radio" name={name} value="pdf" onChange={() => setSelected(1)} />
      </span>
      <GlassSegmented className="candidate-source-segments" aria-label="Candidate source"
        size="sm" options={sourceOptions} value={selected}
        onChange={index => (index === 1 ? pdf : profile).current?.click()} />
    </>
  );
}

export function CandidateSourceControl({ prefix }: { prefix: SourcePrefix }) {
  return <div data-candidate-source={prefix} data-field-control=""><SourceSegments prefix={prefix} /></div>;
}

const actions = {
  "run-ats-score": { label: "Analyze resume", icon: "document", variant: "tinted-filled" },
  "run-ats-match": { label: "Compare with role", icon: "target", variant: "tinted-filled" },
  "generate-cl": { label: "Generate letter", icon: "pen", variant: "tinted-filled" },
  "export-docx": { label: "Word (.docx)", icon: "document", variant: "glass" },
  "export-pdf": { label: "PDF (.pdf)", icon: "document", variant: "glass" },
  "generate-questions": { label: "Generate interview", icon: "chat", variant: "tinted-filled" },
  "start-record": { label: "Start recording", icon: "mic", variant: "glass" },
  "stop-record": { label: "Stop recording", icon: "stop", variant: "tinted" },
  "analyze-answer": { label: "Analyze answer", icon: "sparkles", variant: "tinted-filled" },
  "next-question": { label: "Next question", icon: "chevron-right", variant: "tinted-filled" },
} as const;
type Action = keyof typeof actions;

function ActionButton({ action }: { action: Action }) {
  const { label, icon, variant } = actions[action];
  return (
    <GlassButton id={"btn-" + action} size="sm" icon={<Icon name={icon} />}
      variant={variant} tint={action === "stop-record" ? "var(--danger)" : "var(--accent)"}
      hidden={action === "stop-record"}>
      {label}
    </GlassButton>
  );
}

export function CareerAction({ action }: { action: Action }) {
  return <span data-career-action={action}><ActionButton action={action} /></span>;
}

export function mountCareerControls() {
  mountCareerFields();
  document.querySelectorAll<HTMLElement>("[data-candidate-source]").forEach(host => {
    createRoot(host).render(<SourceSegments prefix={host.dataset.candidateSource as SourcePrefix} />);
  });
  document.querySelectorAll<HTMLElement>("[data-career-action]").forEach(host => {
    createRoot(host).render(<ActionButton action={host.dataset.careerAction as Action} />);
  });
}
