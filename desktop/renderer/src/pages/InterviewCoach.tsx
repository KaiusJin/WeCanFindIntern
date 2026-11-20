import { CareerEditor, SecondaryAction } from "../features/career/CareerFields";
import { CandidateSource } from "../features/career/CareerInputs";
import { CareerAction } from "../features/career/CareerControls";
import { Icon } from "../layouts/AppChrome";

export function InterviewCoach() {
  return (
    <div id="tab-interview" className="tab-pane practice-room" hidden>
      <section className="practice-studio" aria-label="Interview practice">
        <header className="practice-heading">
          <h1>Interview practice</h1>
          <SecondaryAction id="interview-jump-history" label="History" icon="history" />
        </header>
        <div className="studio-session">
          <div className="studio-desk">
            <details id="interview-setup" className="studio-setup" open>
              <summary>
                Session brief
                <Icon name="chevron-right" />
              </summary>
              <div className="interview-setup-fields">
                <div className="field-group">
                  <label className="field-label" htmlFor="interview-jd-text">
                    Target job description
                  </label>
                  <CareerEditor
                    id="interview-jd-text"
                    className="career-textarea"
                    rows={5}
                    placeholder="Paste the job description or start a practice session from a job posting…"
                  />
                </div>
                <CandidateSource prefix="int" />
                <div className="practice-start-actions">
                  <SecondaryAction id="clear-interview" label="Reset" icon="refresh" />
                  <CareerAction action="generate-questions" />
                </div>
              </div>
            </details>
            <div
              id="interview-loading"
              className="loading-state"
              role="status"
              hidden
            >
              <div className="spinner" />
              <span id="interview-loading-text">
                Generating tailored interview questions…
              </span>
            </div>

            <div id="interview-active-card" className="career-card" hidden>
              <div
                className="interview-stepper"
                id="interview-stepper"
                role="group"
                aria-label="Interview questions"
              />

              <div className="question-prompt-box">
                <div className="question-badge" id="interview-q-category">
                  Question 1
                </div>
                <h3 id="interview-q-text" tabIndex={-1}>
                  Interview question will appear here...
                </h3>
              </div>

              <div className="answer-block">
                <div className="answer-block-head">
                  <label
                    className="field-label"
                    htmlFor="interview-answer-text"
                  >
                    Your response
                  </label>
                  <div className="recording-inline">
                    <span
                      className="recording-timer"
                      id="recording-timer"
                      hidden
                    >
                      <span className="rec-dot" />
                      <span id="recording-time-text">00:00</span>
                    </span>
                    <span
                      id="recording-inline-status"
                      className="recording-inline-status"
                      role="status"
                      hidden
                    />
                  </div>
                </div>
                <CareerEditor
                  id="interview-answer-text"
                  className="career-textarea"
                  rows={5}
                  placeholder="Speak your answer, or type it here…"
                />
                <div className="answer-actions">
                  <CareerAction action="start-record" />
                  <CareerAction action="stop-record" />
                  <CareerAction action="analyze-answer" />
                </div>
              </div>
            </div>

            <div id="interview-report-card" className="career-card" hidden>
              <h2 className="practice-report-title">Answer feedback</h2>
              <div className="score-card-header">
                <div className="report-score">
                  <span className="score-number" id="interview-score-num">
                    0
                  </span>
                  <span className="score-max">/100</span>
                </div>
                <div className="score-meta-text">
                  <span className="score-level-pill" id="interview-level-pill">
                    Performance Score
                  </span>
                  <p
                    className="score-summary-text"
                    id="interview-summary-text"
                  />
                </div>
              </div>

              <div id="star-feedback-block" className="report-block" hidden>
                <h4>STAR Method Evaluation</h4>
                <p id="star-feedback-text" className="detail-description" />
              </div>

              <div id="criteria-results-block" className="report-block" hidden>
                <h4>Criteria Check</h4>
                <div id="interview-criteria-wrap" className="criteria-list" />
              </div>

              <div id="transcript-block" className="report-block" hidden>
                <h4>Your Answer Transcript</h4>
                <p
                  id="interview-transcript-text"
                  className="detail-description"
                />
              </div>

              <div className="report-block">
                <h4>Timeline Analysis</h4>
                <div id="interview-timeline-wrap" className="timeline-list" />
              </div>

              <div className="report-block">
                <h4>Improvement Advice</h4>
                <ul id="interview-advice-wrap" className="career-bullet-list" />
              </div>

              <div className="interview-next-actions">
                <CareerAction action="next-question" />
                <p id="interview-completion-status" className="inline-save-status" role="status" aria-atomic="true" />
              </div>
            </div>
          </div>
          <aside
            id="interview-empty"
            className="practice-outline"
            aria-labelledby="practice-outline-title"
          >
            <h2 id="practice-outline-title">Session outline</h2>
            <ol>
              <li>
                <span>Q1</span>
                <div>
                  <strong>Introduction</strong>
                  <p>Introduce yourself in the context of the role.</p>
                </div>
              </li>
              <li>
                <span>Q2–4</span>
                <div>
                  <strong>Work experience</strong>
                  <p>Discuss a recent role and technical follow-ups.</p>
                </div>
              </li>
              <li>
                <span>Q5–7</span>
                <div>
                  <strong>Projects</strong>
                  <p>Explain design decisions, trade-offs and scale.</p>
                </div>
              </li>
            </ol>
          </aside>
        </div>
        <div id="interview-history-card" className="career-card">
          <div className="history-head">
            <h2 id="interview-history-heading" tabIndex={-1}>
              Practice history
            </h2>
            <SecondaryAction id="btn-refresh-history" label="Refresh" icon="refresh" />
          </div>
          <div
            id="interview-trend-summary"
            className="interview-trend-summary"
          />
          <div id="interview-session-list" className="interview-session-list" />
          <div
            id="interview-session-detail"
            role="region"
            aria-label="Practice session detail"
            tabIndex={-1}
            hidden
          />
        </div>
      </section>
    </div>
  );
}
