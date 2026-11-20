import { GlassButton, GlassCard, GlassInput } from "@zakisheriff/liquid-glass";
import type { InputHTMLAttributes, TextareaHTMLAttributes } from "react";
import { createRoot } from "react-dom/client";
import { Icon } from "../../layouts/AppChrome";

export function CareerField(props: InputHTMLAttributes<HTMLInputElement>) {
  return <div data-career-field=""><input {...props} /></div>;
}

export function CareerEditor(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <div data-career-editor=""><textarea {...props} /></div>;
}

export function SecondaryAction({ id, label, icon, removeFor }: {
  id?: string; label: string; icon?: string; removeFor?: string;
}) {
  return <span data-career-secondary={icon ?? ""}>
    <button id={id} type="button" data-remove-for={removeFor} hidden={Boolean(removeFor)}>{label}</button>
  </span>;
}

function DropzoneSurface({ prefix, inputId }: { prefix: string; inputId: string }) {
  return <GlassCard radius="17px" padding="0" intensity={3} className="file-dropzone" data-field-control="" id={prefix + "-dropzone"}>
    <input type="file" aria-label="Choose resume PDF" id={inputId} accept="application/pdf,.pdf" className="file-input" />
    <div className="dropzone-content">
      <span className="dropzone-icon"><Icon name="upload" /></span>
      <span className="dropzone-text" id={prefix + "-file-label"} role="status">Click or drag &amp; drop resume PDF</span>
    </div>
  </GlassCard>;
}

export function ResumeDropField({ prefix, inputId }: { prefix: string; inputId: string }) {
  return <div data-career-drop={prefix}><DropzoneSurface prefix={prefix} inputId={inputId} /></div>;
}

export function mountCareerFields() {
  document.querySelectorAll<HTMLElement>("[data-career-drop]").forEach(host => {
    const input = host.querySelector("input")!;
    createRoot(host).render(<DropzoneSurface prefix={host.dataset.careerDrop!} inputId={input.id} />);
  });

  document.querySelectorAll<HTMLElement>("[data-career-field]").forEach(host => {
    const { id, type, placeholder } = host.querySelector("input")!;
    createRoot(host).render(<GlassInput id={id} type={type} placeholder={placeholder} />);
  });

  document.querySelectorAll<HTMLElement>("[data-career-editor]").forEach(host => {
    const { id, rows, placeholder, className } = host.querySelector("textarea")!;
    createRoot(host).render(
      <GlassCard radius="17px" padding="0" intensity={3} className="writing-glass" data-field-control="">
        <textarea id={id} rows={rows} placeholder={placeholder} className={className} />
      </GlassCard>,
    );
  });

  document.querySelectorAll<HTMLElement>("[data-career-secondary]").forEach(host => {
    const button = host.querySelector("button")!;
    const icon = host.dataset.careerSecondary;
    createRoot(host).render(
      <GlassButton size="sm" type="button" id={button.id || undefined} hidden={Boolean(button.hidden)}
        data-remove-for={button.dataset.removeFor}
        icon={icon ? <Icon name={icon} /> : undefined}>{button.textContent}</GlassButton>,
    );
  });
}
