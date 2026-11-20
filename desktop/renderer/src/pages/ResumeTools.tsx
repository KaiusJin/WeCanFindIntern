import { CareerEditor, SecondaryAction } from "../features/career/CareerFields";
import type { ReactNode } from "react";
import { ResumeUpload } from "../features/career/CareerInputs";
import { CareerAction } from "../features/career/CareerControls";
import { Icon } from "../layouts/AppChrome";

function DiagnosticPage({
  mode,
  children,
}: {
  mode: "score" | "match";
  children: ReactNode;
}) {
  const match = mode === "match";
  const prefix = `ats-${mode}`;
  return (
    <div id={`tab-${prefix}`} className="tab-pane diagnostic-page" hidden>
      <section
        aria-label={match ? "Job match" : "Resume readability"}
        className="diagnostic-sheet"
      >
        <header className="practice-heading">
          <h1>{match ? "Job match" : "Resume readability"}</h1>
          <div className="workspace-actions">
            <SecondaryAction id={`clear-${prefix}`} label="Reset" icon="refresh" />
            <CareerAction action={match ? "run-ats-match" : "run-ats-score"} />
          </div>
        </header>
        <details id={`${prefix}-source`} className="diagnostic-source" open>
          <summary>
            <Icon name="document" />
            <span>{match ? "Comparison sources" : "Resume source"}</span>
            <Icon name="chevron-right" />
          </summary>
          <div className="diagnostic-inputs">
            <div className="diagnostic-upload">
              <label className="field-label" htmlFor={`${prefix}-file-input`}>
                Upload PDF
              </label>
              <ResumeUpload prefix={prefix} inputId={`${prefix}-file-input`} />
              <p className="source-hint">
                {match
                  ? "Use a PDF or paste the resume you want to compare."
                  : "PDFs include layout checks. Pasted text is checked for readability."}
              </p>
            </div>
            <div className="diagnostic-editor">
              <label className="field-label" htmlFor={`${prefix}-resume-text`}>
                Resume text
              </label>
              <CareerEditor
                id={`${prefix}-resume-text`}
                className="career-textarea"
                rows={match ? 4 : 7}
                placeholder="Paste your resume, or edit the text extracted from your PDF…"
              />
              {match && (
                <>
                  <label className="field-label" htmlFor="ats-match-jd-text">
                    Target job description
                  </label>
                  <CareerEditor
                    id="ats-match-jd-text"
                    className="career-textarea"
                    rows={5}
                    placeholder="Paste a description or open Job Match from a job posting…"
                  />
                </>
              )}
            </div>
          </div>
        </details>
        <div
          id={`${prefix}-loading`}
          className="loading-state"
          role="status"
          hidden
        >
          <div className="spinner" />
          <span>
            {match
              ? "Comparing requirements with your resume…"
              : "Analyzing resume readability…"}
          </span>
        </div>
        {children}
      </section>
    </div>
  );
}

function AtsFeedback({ prefix }: { prefix: string }) {
  return (
    <div className="report-block" hidden>
      <h3>AI feedback</h3>
      <p id={`${prefix}-ai-status`} className="muted-copy" role="status" hidden />
      <div id={`${prefix}-ai-result`} className="ats-ai-feedback" hidden>
        <p id={`${prefix}-ai-summary`} className="score-summary-text" />
        <div
          id={`${prefix}-ai-strengths-block`}
          className="ats-ai-feedback-group"
        >
          <strong>Strengths</strong>
          <ul id={`${prefix}-ai-strengths`} className="career-bullet-list" />
        </div>
        <div className="ats-ai-feedback-group">
          <strong>Priority improvements</strong>
          <ul id={`${prefix}-ai-improvements`} className="career-bullet-list" />
        </div>
      </div>
    </div>
  );
}

export function ResumeScore() {
  return (
    <DiagnosticPage mode="score">
      <article
        id="ats-readiness-result"
        className="diagnostic-report"
        aria-labelledby="ats-score-report-heading"
        hidden
      >
        <h2 id="ats-score-report-heading" tabIndex={-1}>
          Readability report
        </h2>
        <div className="score-card-header">
          <div className="report-score">
            <span className="score-number" id="ats-readiness-score">
              0
            </span>
            <span className="score-max">/100</span>
          </div>
          <div className="score-meta-text">
            <span className="score-level-pill" id="ats-readiness-level">
              Readiness
            </span>
            <p className="score-summary-text" id="ats-readiness-summary" />
          </div>
        </div>
        <p id="ats-readiness-method" className="muted-copy" />
        <div className="report-block">
          <h3>Parsing issues</h3>
          <ul id="ats-readiness-issues" className="career-bullet-list" />
          <p id="ats-readiness-limitations" className="muted-copy" />
        </div>
        <div className="report-block">
          <h3>Score breakdown</h3>
          <div id="ats-readiness-breakdown" className="ats-breakdown-list" />
        </div>
        <AtsFeedback prefix="ats-score" />
      </article>
    </DiagnosticPage>
  );
}

export function JobMatch() {
  return (
    <DiagnosticPage mode="match">
      <article
        id="ats-match-result"
        className="diagnostic-report"
        aria-labelledby="ats-match-report-heading"
        hidden
      >
        <h2 id="ats-match-report-heading" tabIndex={-1}>
          Match report
        </h2>
        <div className="score-card-header">
          <div className="report-score">
            <span className="score-number" id="ats-match-score">
              0
            </span>
            <span className="score-max">/100</span>
          </div>
          <div className="score-meta-text">
            <span className="score-level-pill" id="ats-match-level">
              Match level
            </span>
            <p className="score-summary-text" id="ats-match-summary" />
          </div>
        </div>
        <div className="report-block">
          <h3>Score breakdown</h3>
          <div id="ats-match-breakdown" className="ats-breakdown-list" />
        </div>
        <div className="report-block">
          <h3>Matched evidence</h3>
          <div id="ats-match-evidence" className="tag-badges-grid" />
        </div>
        <div className="report-block">
          <h3>Missing or partial requirements</h3>
          <div id="ats-match-gaps" className="tag-badges-grid gap-grid" />
        </div>
        <div id="ats-eligibility-block" className="report-block" hidden>
          <h3>Eligibility flags from the job description</h3>
          <ul id="ats-eligibility-flags" className="career-bullet-list" />
        </div>
        <div className="report-block">
          <h3>Evidence-backed improvements</h3>
          <ul id="ats-match-suggestions" className="career-bullet-list" />
        </div>
        <AtsFeedback prefix="job-match" />
      </article>
    </DiagnosticPage>
  );
}
