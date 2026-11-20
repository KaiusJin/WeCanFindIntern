"use client";

import { GlassButton, GlassSheet } from "@zakisheriff/liquid-glass";
import { useCallback, useLayoutEffect, useRef, useState } from "react";

type Decision = {
  title: string;
  message: string;
  kind?: "fault";
  confirmLabel?: string;
  destructive?: boolean;
  resolve: (accepted: boolean) => void;
};

type Request = { decision: Decision; id: number; trigger: HTMLElement | null };

function DecisionSheet({ request, onDone }: { request: Request; onDone: () => void }) {
  const { decision, trigger } = request;
  const [dialog, setDialog] = useState<HTMLDialogElement | null>(null);
  const [open, setOpen] = useState(true);
  const answer = useRef<boolean | null>(null);
  const isFault = decision.kind === "fault";
  const firstButton = useRef<HTMLButtonElement>(null);

  useLayoutEffect(() => {
    if (dialog && !dialog.open) dialog.showModal();
  }, [dialog]);

  function respond(accepted: boolean) {
    if (answer.current !== null) return;
    answer.current = accepted;
    setOpen(false);
  }

  const contentRef = useCallback((content: HTMLDivElement | null) => {
    if (!content) return;
    // The outer native dialog keeps focus and other open panels in the right
    // order. The library supplies the draggable sheet and its exit animation.
    const surface = content.closest(".lg-sheet")!;
    surface.setAttribute("role", "document");
    surface.removeAttribute("aria-modal");
    const frame = requestAnimationFrame(() => firstButton.current?.focus({ preventScroll: true }));
    return () => {
      cancelAnimationFrame(frame);
      if (answer.current === null) return;
      dialog?.close();
      if (trigger?.isConnected) trigger.focus({ preventScroll: true });
      decision.resolve(answer.current);
      onDone();
    };
  }, [decision, dialog, onDone, trigger]);

  return (
    <dialog ref={setDialog} className="app-modal-sheet feedback-sheet" aria-labelledby="feedback-sheet-title"
      aria-describedby="feedback-sheet-message" onCancel={event => { event.preventDefault(); respond(false); }}
      onClick={event => { if (event.target === event.currentTarget) respond(false); }}>
      {dialog && <GlassSheet open={open} container={dialog} onClose={() => respond(false)}
        aria-label={decision.title}>
        <div ref={contentRef} className="feedback-sheet-content">
          <h2 id="feedback-sheet-title">{decision.title}</h2>
          <p id="feedback-sheet-message">{decision.message}</p>
          <div className="feedback-sheet-actions">
            {!isFault && <GlassButton ref={firstButton} size="sm" onClick={() => respond(false)}>Cancel</GlassButton>}
            <GlassButton ref={isFault ? firstButton : undefined} size="sm" destructive={decision.destructive}
              variant={decision.destructive ? "filled" : "tinted-filled"} onClick={() => respond(true)}>
              {decision.confirmLabel || (isFault ? "Done" : "Confirm")}
            </GlassButton>
          </div>
        </div>
      </GlassSheet>}
    </dialog>
  );
}

export function FeedbackSheets() {
  const [requests, setRequests] = useState<Request[]>([]);
  const nextId = useRef(0);
  useLayoutEffect(() => {
    const enqueue = (event: Event) => {
      event.preventDefault();
      const request = {
        decision: (event as CustomEvent<Decision>).detail,
        trigger: document.activeElement instanceof HTMLElement ? document.activeElement : null,
        id: ++nextId.current,
      };
      setRequests(items => [...items, request]);
    };
    document.addEventListener("wcfi:decision", enqueue);
    return () => document.removeEventListener("wcfi:decision", enqueue);
  }, []);
  const next = useCallback(() => setRequests(items => items.slice(1)), []);
  return requests[0] ? <DecisionSheet key={requests[0].id} request={requests[0]} onDone={next} /> : null;
}
