import { ResumeDropField, SecondaryAction } from "./CareerFields";
import type { ReactNode } from "react";
import { CandidateSourceControl } from "./CareerControls";

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
