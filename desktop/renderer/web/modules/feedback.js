// Feedback for the DOM-based feature controllers. The React sheet is registered
// before main.js runs. Missing or dismissed decisions always resolve to false.
export function confirmDecision(options) {
  return new Promise(resolve => {
    const event = new CustomEvent("wcfi:decision", {
      cancelable: true, detail: { ...options, resolve },
    });
    if (document.dispatchEvent(event)) resolve(false);
  });
}

export function showSystemFault(message, { title = "Application problem", guidance } = {}) {
  return confirmDecision({
    kind: "fault", title, message: [message?.message || String(message), guidance].filter(Boolean).join("\n\n"),
    confirmLabel: "Done",
  });
}

let statusRegion;

// Announce a visible state change without adding another visual notification.
export function announceStatus(message) {
  if (!statusRegion) {
    statusRegion = document.createElement("div");
    statusRegion.className = "feedback-announcement";
    statusRegion.setAttribute("role", "status");
    statusRegion.setAttribute("aria-atomic", "true");
    document.body.append(statusRegion);
  }
  statusRegion.textContent = "";
  queueMicrotask(() => { statusRegion.textContent = message; });
}

// Keep success next to its trigger. Edits made during a request invalidate its
// success label, so an older response cannot mark newer input as saved.
export function createActionFeedback(target, {
  scope, pending = "Saving…", success = "Saved", resetAfter = 0,
} = {}) {
  const button = typeof target === "string" ? document.querySelector(target) : target;
  const label = button.querySelector(".lg-button__label") || button;
  const idle = label.textContent;
  const root = typeof scope === "string" ? document.querySelector(scope) : scope;
  let revision = 0;
  let active;
  let timer;
  button.dataset.actionFeedback = "";
  label.setAttribute("aria-live", "polite");
  label.setAttribute("aria-atomic", "true");

  function reset({ cancelPending = false } = {}) {
    revision++;
    clearTimeout(timer);
    if (cancelPending && active) {
      button.disabled = active.disabled;
      button.removeAttribute("aria-busy");
      active = null;
    }
    if (!active) {
      label.textContent = idle;
      delete button.dataset.feedback;
    }
  }
  root?.addEventListener("input", reset);
  root?.addEventListener("change", reset);

  function begin() {
    clearTimeout(timer);
    // Normally prevented by the disabled button; also guard programmatic calls.
    if (active) return () => false;
    const request = { revision, disabled: button.disabled };
    active = request;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.dataset.feedback = "pending";
    label.textContent = pending;
    return (succeeded = false) => {
      if (active !== request) return false;
      active = null;
      button.disabled = request.disabled;
      button.removeAttribute("aria-busy");
      const current = succeeded && revision === request.revision;
      label.textContent = current ? success : idle;
      if (current) {
        button.dataset.feedback = "success";
        if (resetAfter) timer = setTimeout(reset, resetAfter);
      } else delete button.dataset.feedback;
      return current;
    };
  }
  return { begin, reset };
}

const notices = new Map();
let toastHost;

function placeToastHost() {
  const modal = Array.from(document.querySelectorAll("dialog:modal")).at(-1);
  const parent = modal || document.body;
  if (toastHost.parentNode !== parent) parent.append(toastHost);
  if (!toastHost.matches(":popover-open")) toastHost.showPopover();
}

export function showToast(message, { title, variant = "info", action } = {}) {
  const detail = String(message?.message || message || "").trim();
  if (!detail && !title) return;
  const key = action ? Symbol() : JSON.stringify([variant, title, detail]);
  if (!toastHost) {
    toastHost = document.createElement("div");
    toastHost.className = "toast-region";
    toastHost.popover = "manual";
    toastHost.setAttribute("aria-label", "Notifications");
    document.addEventListener("close", event => {
      if (event.target === toastHost.parentElement && notices.size) placeToastHost();
    }, true);
  }
  placeToastHost();
  if (notices.has(key)) {
    notices.get(key).start();
    return;
  }

  const notice = document.createElement("div");
  notice.className = "feedback-toast";
  notice.dataset.variant = variant;
  const copy = document.createElement("div");
  copy.className = "toast-copy";
  copy.setAttribute("role", variant === "error" ? "alert" : "status");
  const close = document.createElement("button");
  close.type = "button";
  close.setAttribute("aria-label", "Dismiss notification");
  close.textContent = "×";
  notice.append(copy, close);
  toastHost.append(notice);
  if (title) {
    const heading = document.createElement("strong");
    heading.textContent = title;
    copy.append(heading);
  }
  if (detail && detail !== title) {
    const text = document.createElement("span");
    text.textContent = detail;
    copy.append(text);
  }
  if (action) {
    const link = document.createElement("button");
    link.type = "button";
    link.className = "toast-action";
    link.textContent = action.label;
    link.addEventListener("click", () => { dismiss(); action.onClick(); });
    notice.append(link);
  }

  let timer;
  function stop() { clearTimeout(timer); }
  function start() {
    stop();
    if (!notice.matches(":hover, :focus-within")) timer = setTimeout(dismiss, variant === "error" ? 6500 : 4200);
  }
  function dismiss() {
    stop();
    notices.delete(key);
    notice.remove();
    if (!notices.size) toastHost.hidePopover();
  }
  notices.set(key, { start, dismiss });
  if (notices.size > 2) notices.values().next().value.dismiss();
  close.addEventListener("click", dismiss);
  notice.addEventListener("mouseenter", stop);
  notice.addEventListener("mouseleave", start);
  notice.addEventListener("focusin", stop);
  notice.addEventListener("focusout", () => queueMicrotask(start));
  start();
}

const errors = new WeakMap();
let nextErrorId = 0;

export function clearFieldError(target) {
  const field = typeof target === "string" ? document.querySelector(target) : target;
  const previous = errors.get(field);
  if (!previous) return;
  const { host, note, clear, originalInvalid } = previous;
  host.removeEventListener("input", clear);
  host.removeEventListener("change", clear);
  const descriptions = (field.getAttribute("aria-describedby") || "").split(/\s+/).filter(id => id && id !== note.id);
  if (descriptions.length) field.setAttribute("aria-describedby", descriptions.join(" "));
  else field.removeAttribute("aria-describedby");
  if (originalInvalid === null) field.removeAttribute("aria-invalid");
  else field.setAttribute("aria-invalid", originalInvalid);
  host.classList.remove("has-field-error");
  note.remove();
  errors.delete(field);
}

export function showFieldError(target, message, { focus = true } = {}) {
  const field = typeof target === "string" ? document.querySelector(target) : target;
  if (!field) return showToast(message, { variant: "error" });
  const host = field.closest("[data-field-control], .lg-input, .settings-key-field") || field;
  for (let section = host.parentElement; section; section = section.parentElement) {
    if (section.tagName === "DETAILS") section.open = true;
  }
  let state = errors.get(field);
  if (!state) {
    const note = document.createElement("p");
    note.id = "field-error-" + ++nextErrorId;
    note.className = "field-error-message";
    const originalInvalid = field.getAttribute("aria-invalid");
    field.setAttribute("aria-invalid", "true");
    field.setAttribute("aria-describedby", [field.getAttribute("aria-describedby"), note.id].filter(Boolean).join(" "));
    host.classList.add("has-field-error");
    host.after(note);
    const clear = () => clearFieldError(field);
    host.addEventListener("input", clear);
    host.addEventListener("change", clear);
    state = { host, note, clear, originalInvalid };
    errors.set(field, state);
  }
  state.note.textContent = message;
  if (focus) {
    field.focus({ preventScroll: true });
    host.scrollIntoView({ block: "nearest", inline: "nearest" });
    state.note.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
}
