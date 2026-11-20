import { ResumeDropField, SecondaryAction } from "./CareerFields";
import type { ReactNode } from "react";
import { PageHeader } from "../../layouts/PageHeader";
import { Icon } from "../../layouts/AppChrome";
import { CandidateSourceControl } from "./CareerControls";

type PageProps = {
  id: string;
  title: string;
  description: string;
  children: ReactNode;
};

export function CareerPage({ id, title, description, children }: PageProps) {
  return (
    <div id={id} className="tab-pane" hidden>
      <PageHeader title={title} description={description} />
      <section className="career-workspace">{children}</section>
    </div>
  );
}

export function ResumeUpload({ prefix, inputId }: { prefix: string; inputId: string }) {
  return (
    <>
      <ResumeDropField prefix={prefix} inputId={inputId} />
      <SecondaryAction label="Remove PDF" removeFor={`${prefix}-dropzone`} />
    </>
  );
}

export function CandidateSource({ prefix, children }: { prefix: "int" | "cl"; children?: ReactNode }) {
  return (
    <div className="field-group">
      <span className="field-label" id={`${prefix}-source-label`}>
        Candidate source
      </span>
      <CandidateSourceControl prefix={prefix} />
      <div id={`${prefix}-pdf-source`} className="cl-source-panel" hidden>
        <ResumeUpload prefix={prefix} inputId={`${prefix}-resume-pdf`} />
      </div>
      {children}
      <label className="field-label cl-resume-preview-label" htmlFor={`${prefix}-resume-text`} hidden>
        Candidate context preview
      </label>
      <textarea id={`${prefix}-resume-text`} className="career-textarea" rows={4} hidden />
    </div>
  );
}

export function ReportPending({
  id,
  icon,
  title,
  children,
}: {
  id: string;
  icon: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <div id={id} className="empty-state">
      <span className="report-empty-icon">
        <Icon name={icon} />
      </span>
      <div>
        <h3>{title}</h3>
        <p>{children}</p>
      </div>
    </div>
  );
}
