import { GlassButton, GlassInput } from "@zakisheriff/liquid-glass";
import { useLayoutEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { Icon } from "../layouts/AppChrome";

export function AssistantDialogs() {
  return <div id="assistant-dialog-controls"><AssistantDialogContents /></div>;
}

function AttachmentSearch() {
  const form = useRef<HTMLFormElement>(null);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  useLayoutEffect(() => {
    const element = form.current!;
    const update = (event: Event) => setBusy((event as CustomEvent<boolean>).detail);
    element.addEventListener("assistant:attachment-search", update);
    return () => element.removeEventListener("assistant:attachment-search", update);
  }, []);
  return (
    <form id="agent-attach-search-form" className="agent-attach-search" ref={form} onReset={(event) => {
      event.preventDefault();
      flushSync(() => setQuery(""));
    }}>
      <GlassInput id="agent-attach-query" type="search" label="Find a role" aria-label="Find a role"
        placeholder="Title, company, skill, or job ID" autoComplete="off" value={query}
        onChange={(event) => setQuery(event.currentTarget.value)} />
      <GlassButton id="agent-attach-search-button" variant="tinted" type="submit" disabled={busy}>
        {busy ? "Searching…" : "Search"}
      </GlassButton>
    </form>
  );
}

export function AssistantDialogContents() {
  return (
    <>
      <aside id="agent-attach-dialog" className="assistant-side-panel" aria-labelledby="agent-attach-title" hidden>
        <header className="assistant-panel-header">
          <h2 id="agent-attach-title">Attach jobs</h2>
          <GlassButton id="close-agent-attach-dialog" size="sm" icon={<Icon name="close" />} aria-label="Close Attach jobs" />
        </header>
        <p className="assistant-panel-description">Add up to 5 roles to this conversation.</p>
        <div className="assistant-panel-content">
          <div id="agent-current-job-option" className="agent-current-job-option" hidden></div>
          <div className="agent-attach-search-section">
            <AttachmentSearch />
          </div>
          <div className="agent-attach-results-heading">
            <strong>Available roles</strong>
          </div>
          <div id="agent-attach-results" className="agent-attach-results" aria-live="polite" role="list"></div>
        </div>
        <div className="assistant-panel-actions">
          <GlassButton id="done-agent-attach-dialog" size="sm">Done</GlassButton>
        </div>
      </aside>
      <aside id="agent-memory-dialog" className="assistant-side-panel" aria-labelledby="agent-memory-title" hidden>
        <header className="assistant-panel-header">
          <h2 id="agent-memory-title">Career Memory <span id="agent-memory-dialog-count" className="agent-memory-dialog-count">0</span></h2>
          <GlassButton id="close-agent-memory-dialog" size="sm" icon={<Icon name="close" />} aria-label="Close Career Memory" />
        </header>
        <p className="assistant-panel-description">Details from your conversations that personalize future answers.</p>
        <div className="assistant-panel-content">
          <div id="agent-memory-dialog-status" className="agent-memory-status" role="status">Nothing saved yet.</div>
          <div id="agent-memory-dialog-list" className="agent-memory-list"></div>
        </div>
      </aside>
    </>
  );
}
