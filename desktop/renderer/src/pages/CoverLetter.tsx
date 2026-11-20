import { CareerEditor, CareerField, SecondaryAction } from "../features/career/CareerFields";
import { CandidateSource } from "../features/career/CareerInputs";
import { CareerAction } from "../features/career/CareerControls";
import { Icon } from "../layouts/AppChrome";

export function CoverLetter() {
  return (
    <div id="tab-cover-letter" className="tab-pane" hidden>
      <section className="letter-workspace career-workspace">
        <header className="letter-heading">
          <h1>Cover Letter</h1>
          <div className="workspace-actions">
            <SecondaryAction id="clear-cover-letter" label="Reset" icon="refresh" />
            <CareerAction action="generate-cl" />
          </div>
        </header>
        <details id="cl-brief" className="letter-brief" open>
          <summary>
            <Icon name="document" />
            Letter brief
            <Icon name="chevron-down" />
          </summary>
          <div className="letter-intake">
            <aside className="letter-source" aria-label="Candidate information">
              <CandidateSource prefix="cl">
                <div
                  id="cl-contact-section"
                  className="cl-contact-section"
                  hidden
                >
                  <span className="field-label">Contact Details</span>
                  <div className="career-config-grid">
                    <div>
                      <label className="subfield-label" htmlFor="cl-full-name">
                        Full Name
                      </label>
                      <CareerField
                        type="text"
                        id="cl-full-name"
                        className="career-input"
                        placeholder="e.g. Alex Chen"
                      />
                    </div>
                    <div>
                      <label className="subfield-label" htmlFor="cl-email">
                        Email
                      </label>
                      <CareerField
                        type="email"
                        id="cl-email"
                        className="career-input"
                        placeholder="e.g. alex@example.com"
                      />
                    </div>
                    <div>
                      <label className="subfield-label" htmlFor="cl-phone">
                        Phone
                      </label>
                      <CareerField
                        type="text"
                        id="cl-phone"
                        className="career-input"
                        placeholder="e.g. +1 (555) 019-2834"
                      />
                    </div>
                    <div>
                      <label className="subfield-label" htmlFor="cl-linkedin">
                        LinkedIn / Portfolio
                      </label>
                      <CareerField
                        type="text"
                        id="cl-linkedin"
                        className="career-input"
                        placeholder="e.g. linkedin.com/in/alex"
                      />
                    </div>
                  </div>
                </div>
              </CandidateSource>
            </aside>
            <div className="letter-role">
              <div className="field-group">
                <label className="field-label" htmlFor="cl-jd-text">
                  Target job description *
                </label>
                <CareerEditor
                  id="cl-jd-text"
                  className="career-textarea"
                  rows={6}
                  placeholder="Paste the target job description and requirements here…"
                />
              </div>

              <details className="cl-additional-section">
                <summary>Additional job details</summary>
                <div className="career-config-grid">
                  <div>
                    <label className="field-label" htmlFor="cl-job-title">
                      Job Title
                    </label>
                    <CareerField
                      type="text"
                      id="cl-job-title"
                      className="career-input"
                      placeholder="e.g. Software Engineering Intern"
                    />
                  </div>
                  <div>
                    <label className="field-label" htmlFor="cl-company-name">
                      Company Name
                    </label>
                    <CareerField
                      type="text"
                      id="cl-company-name"
                      className="career-input"
                      placeholder="e.g. Datadog"
                    />
                  </div>
                  <div>
                    <label
                      className="field-label"
                      htmlFor="cl-company-location"
                    >
                      Company Location
                    </label>
                    <CareerField
                      type="text"
                      id="cl-company-location"
                      className="career-input"
                      placeholder="e.g. Toronto, ON"
                    />
                  </div>
                  <div>
                    <label className="field-label" htmlFor="cl-hiring-manager">
                      Hiring Manager / Team
                    </label>
                    <CareerField
                      type="text"
                      id="cl-hiring-manager"
                      className="career-input"
                      placeholder="e.g. Engineering Team"
                    />
                  </div>
                </div>
                <label className="field-label" htmlFor="cl-company-info">
                  Company Information
                </label>
                <CareerEditor
                  id="cl-company-info"
                  className="career-textarea"
                  rows={3}
                  placeholder="Product, mission, or team details…"
                />
              </details>

            </div>

          </div>
        </details>
        <section
          id="cl-draft"
          className="letter-draft"
          aria-labelledby="cl-draft-title"
          hidden
        >
          <div className="letter-draft-tools">
            <h2 id="cl-draft-title">Letter draft</h2>
            <div id="cl-export-group" className="export-btn-group" hidden>
              <CareerAction action="export-docx" />
              <CareerAction action="export-pdf" />
            </div>
          </div>
          <div id="cl-loading" className="loading-state" role="status" hidden>
            <div className="spinner" />
            <span id="cl-loading-message">
              Writer AI is drafting your cover letter…
            </span>
          </div>

          <div id="cl-result-card" className="career-card" hidden>
            <section id="cl-review-card" className="cl-review-card" aria-label="Letter review" hidden />
            <label className="field-label" htmlFor="cl-editor-text">
              Letter text
            </label>
            <CareerEditor
              id="cl-editor-text"
              className="career-textarea cl-editor"
              rows={18}
            />
          </div>
        </section>
      </section>
    </div>
  );
}
