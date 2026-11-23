import { GlassButton, GlassCard } from "@zakisheriff/liquid-glass";
import { useLayoutEffect, useRef, useState } from "react";
import { Icon } from "../layouts/AppChrome";
import { AssistantDialogs } from "./AssistantDialogs";

const suggestions = [
  ["target", "Match my profile", "Recommend internships that match my profile"],
  ["board", "Review applications", "Show me the jobs currently in my Tracker"],
  [
    "pin",
    "Find Toronto internships",
    "Find software engineering internships in Toronto",
  ],
] as const;

export function Assistant() {
  return (
    <div id="tab-agent" className="tab-pane" hidden>
      <section className="agent-workspace" aria-label="AI Assistant">
        <div id="agent-layout" className="conversation-canvas">
          <header className="conversation-tools">
            <h1 id="agent-session-title">New conversation</h1>
            <div id="assistant-toolbar-controls"><AssistantToolbar /></div>
          </header>
          <div className="agent-main">
            <template id="agent-welcome-template">
              <div className="agent-welcome">
                <span className="assistant-mark" aria-hidden="true">
                  <Icon name="sparkles" />
                </span>
                <h2>What are you working on?</h2>
                <div className="agent-suggestion-list">
                  {suggestions.map(([icon, label, prompt]) => (
                    <GlassButton type="button" size="sm" key={icon} data-agent-prompt={prompt}>
                      <Icon name={icon} />
                      <span>{label}</span>
                      <Icon name="arrow-up-right" />
                    </GlassButton>
                  ))}
                </div>
              </div>
            </template>
            <div
              id="agent-chat"
              className="agent-chat"
              role="log"
              aria-label="Conversation"
              aria-live="polite"
            />
            <div id="assistant-composer-controls" className="agent-composer-shell">
              <AssistantComposer />
            </div>
          </div>
          <aside id="agent-job-detail-pane" className="assistant-side-panel assistant-job-reader" aria-label="Job details" hidden>
            <header className="assistant-panel-header">
              <h2>Job details</h2>
              <GlassButton id="close-agent-job-detail" type="button" size="sm" variant="clear" aria-label="Close job details"
                icon={<Icon name="close" />} />
            </header>
            <div id="agent-job-detail" className="job-detail-content" />
          </aside>
          <AssistantDialogs />
        </div>
      </section>
    </div>
  );
}


export function AssistantToolbar() {
  return (
    <div className="conversation-actions">
      <details id="agent-history" className="conversation-history">
        <summary className="glass" aria-controls="agent-session-list">
          <Icon name="history" />
          <span>History</span>
        </summary>
        <GlassCard className="conversation-history-menu" padding="10px" radius="20px">
          <h2>Recent conversations</h2>
          <div
            id="agent-session-list"
            className="agent-session-list"
            aria-label="Conversations"
          >
            <p className="conversation-list-status" role="status">
              Loading conversations…
            </p>
          </div>
        </GlassCard>
      </details>
      <GlassButton id="agent-memory-trigger" size="sm" icon={<Icon name="layers" />}
        title="Career Memory" aria-label="Career Memory" aria-expanded="false" aria-controls="agent-memory-dialog">
        Memory
      </GlassButton>
      <GlassButton id="agent-new-session" size="sm" icon={<Icon name="pen" />}
        title="New conversation" aria-label="New conversation">
        New chat
      </GlassButton>
    </div>
  );
}

export function AssistantComposer() {
  const form = useRef<HTMLFormElement>(null);
  const [state, setState] = useState({ attached: 0, streaming: false });
  useLayoutEffect(() => {
    const element = form.current!;
    const update = (event: Event) => setState((event as CustomEvent<typeof state>).detail);
    element.addEventListener("assistant:composer", update);
    return () => element.removeEventListener("assistant:composer", update);
  }, []);

  const attachLabel = state.attached ? `Attach ${state.attached} ${state.attached === 1 ? "job" : "jobs"}` : "Attach jobs";
  const sendLabel = state.streaming ? "Stop generating" : "Send message";
  return (
    <form id="agent-form" ref={form} className="agent-composer" autoComplete="off">
      <GlassCard className="agent-composer-card" radius="26px" padding="14px">
        <div data-field-control="" className="assistant-message-field">
          <textarea
            id="agent-input"
            className="career-input"
            aria-label="Message the assistant"
            rows={1}
            maxLength={4000}
            placeholder="Message the assistant…"
          />
        </div>
        <GlassButton id="agent-attach-jobs" variant="glass" size="sm" icon={<Icon name="plus" />}
          aria-label={attachLabel} title={attachLabel} aria-expanded="false" aria-controls="agent-attach-dialog">
          {attachLabel}
        </GlassButton>
        <span id="agent-reply-status" role="status">{state.streaming ? "Generating reply…" : ""}</span>
        <GlassButton
          className="agent-send-button"
          size="sm"
          variant="filled"
          type="submit"
          aria-label={sendLabel}
          title={sendLabel}
          icon={
            <svg className="agent-send-icon" viewBox="0 0 24 24" aria-hidden="true">
              {state.streaming
                ? <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" />
                : <path d="M12 19V5m-6 6 6-6 6 6" />}
            </svg>
          }
        />
      </GlassCard>
    </form>
  );
}
