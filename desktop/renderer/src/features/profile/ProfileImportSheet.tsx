import { GlassButton, GlassSheet } from "@zakisheriff/liquid-glass";
import { useCallback, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { Icon } from "../../layouts/AppChrome";

type ImportMode = "resume" | "latex";
type PreviewImport = (file: File | undefined, mode: ImportMode) => Promise<boolean>;

export function ProfileImportSheet() {
  const dialog = useRef<HTMLDialogElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const focusAfterClose = useRef<HTMLElement | null>(null);
  const previewReady = useRef(false);
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<ImportMode>("resume");
  const [fileName, setFileName] = useState("");
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState(false);
  const [previewImport, setPreviewImport] = useState<PreviewImport | null>(null);
  useLayoutEffect(() => {
    const host = dialog.current!;
    const ready = (event: Event) => setPreviewImport(() => (event as CustomEvent<PreviewImport>).detail);
    host.addEventListener("profile:import-ready", ready);
    return () => host.removeEventListener("profile:import-ready", ready);
  }, []);
  const isResume = mode === "resume";
  const title = isResume ? "Autofill from resume" : "Autofill from LaTeX";

  function show(mode: ImportMode, trigger: HTMLElement) {
    if (busy || !previewImport) return;
    focusAfterClose.current = trigger;
    setMode(mode);
    dialog.current!.showModal();
    setOpen(true);
  }

  const finishClose = useCallback(() => {
    if (dialog.current?.open) dialog.current.close();
    focusAfterClose.current?.focus();
    if (previewReady.current) {
      previewReady.current = false;
      dialog.current?.dispatchEvent(new Event("profile:draft-ready"));
    }
  }, []);

  function dismiss() {
    // The library listens for Escape on window. A native error dialog opened
    // above this sheet must get the first opportunity to handle that key.
    const focusedDialog = document.activeElement?.closest("dialog");
    if (!dialog.current?.open || (focusedDialog && focusedDialog !== dialog.current)) return;
    setOpen(false);
    if (!dialog.current?.querySelector(".lg-sheet")) finishClose();
  }

  const sheetContent = useCallback((content: HTMLDivElement | null) => {
    if (!content) return;
    // The native modal supplies top-layer semantics and inertness. Its
    // animated sheet is the document inside that modal.
    const surface = content.closest<HTMLElement>(".lg-sheet")!;
    surface.setAttribute("role", "document");
    surface.removeAttribute("aria-modal");
    return finishClose;
  }, [finishClose]);

  function wrapTab(event: KeyboardEvent<HTMLDialogElement>) {
    if (event.key !== "Tab") return;
    const buttons = event.currentTarget.querySelectorAll<HTMLElement>("button:enabled, textarea:enabled");
    if (!buttons.length) return;
    const first = buttons[0], last = buttons[buttons.length - 1];
    if (event.shiftKey && (event.target === first || event.target === event.currentTarget.querySelector(".lg-sheet"))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && event.target === last) {
      event.preventDefault();
      first.focus();
    }
  }

  async function preview() {
    if (busy || !previewImport) return;
    const file = isResume ? fileInput.current?.files?.[0] : new File([source], "pasted-resume.tex", { type: "application/x-tex" });
    setBusy(true);
    try {
      if (await previewImport(file, mode)) {
        focusAfterClose.current = document.getElementById("profile-apply-draft");
        previewReady.current = true;
        if (dialog.current?.open) setOpen(false);
        else finishClose();
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <GlassButton id="profile-autofill-resume" size="sm" icon={<Icon name="upload" />} disabled={!previewImport}
        aria-disabled={busy || !previewImport} onClick={(event) => show("resume", event.currentTarget)}>
        Import resume
      </GlassButton>
      <GlassButton id="profile-autofill-latex" size="sm" disabled={!previewImport} aria-disabled={busy || !previewImport}
        onClick={(event) => show("latex", event.currentTarget)}>Import LaTeX</GlassButton>
      <div id="profile-draft-review" className="profile-draft-review" role="group" aria-label="Import draft" hidden>
        <details>
          <summary>Import draft</summary>
          <p id="profile-draft-summary"></p>
        </details>
        <GlassButton id="profile-apply-draft" size="sm" variant="tinted" tint="var(--accent)">Apply import</GlassButton>
        <GlassButton id="profile-discard-draft" size="sm">Discard</GlassButton>
      </div>
      <dialog id="profile-autofill-dialog" className="app-modal-sheet profile-import-sheet" ref={dialog} aria-label={title}
        onCancel={(event) => { event.preventDefault(); dismiss(); }} onKeyDown={wrapTab}>
        <input id="profile-resume-file" ref={fileInput} type="file" accept=".pdf,application/pdf" hidden
          onChange={(event) => setFileName(event.currentTarget.files?.[0]?.name || "")} />
        <GlassSheet open={open} onClose={dismiss} container={dialog.current} aria-label={title}>
          <div ref={sheetContent} className="profile-import-content">
            <header className="profile-autofill-dialog-head">
              <h2 id="profile-autofill-title">{title}</h2>
              <GlassButton id="profile-autofill-close" size="sm" icon={<Icon name="close" />}
                aria-label="Close import" onClick={dismiss} />
            </header>
            <p className="profile-help" id="profile-autofill-guidance">
              {isResume ? "Choose a text-based PDF to create an editable draft." : "Paste your LaTeX source to create an editable draft."}
              {" "}Review the fields before applying your import.
            </p>
            {isResume ? (
              <div className="profile-autofill-panel" data-field-control="">
                <GlassButton id="profile-resume-file-picker" icon={<Icon name="upload" />} onClick={() => fileInput.current?.click()}>
                  Choose PDF resume
                </GlassButton>
                <span id="profile-resume-file-name" className="profile-autofill-file-name" role="status">
                  {fileName || "No file selected"}
                </span>
              </div>
            ) : (
              <textarea id="profile-latex-source" aria-label="LaTeX source" className="career-textarea profile-code-input"
                rows={8} spellCheck={false} value={source} onChange={(event) => setSource(event.currentTarget.value)}
                placeholder={"\\documentclass{article}\n\\begin{document}\n...\n\\end{document}"} />
            )}
            <div className="profile-autofill-dialog-actions">
              <GlassButton id="profile-autofill-submit" variant="tinted-filled" disabled={busy} onClick={preview}>
                {busy ? "Reading document…" : "Preview import"}
              </GlassButton>
            </div>
          </div>
        </GlassSheet>
      </dialog>
    </>
  );
}
