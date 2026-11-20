import type { KeyboardEvent } from "react";

const triggerSelector = 'button[aria-haspopup="menu"]';

export function focusGlassMenuTrigger(container: ParentNode | null) {
  container?.querySelector<HTMLButtonElement>(triggerSelector)?.focus({ preventScroll: true });
}

export function handleGlassMenuKey(event: KeyboardEvent<HTMLElement>) {
  const { key, currentTarget } = event;
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(key)) return;
  const trigger = currentTarget.querySelector<HTMLButtonElement>(triggerSelector);
  if (!trigger) return;
  event.preventDefault();
  if (trigger.getAttribute("aria-expanded") !== "true") {
    trigger.click();
    return;
  }

  const items = Array.from(currentTarget.querySelectorAll<HTMLButtonElement>(
    'button:is([role="menuitem"], [role="menuitemradio"]):not(:disabled)',
  ));
  if (!items.length) return;
  const index = items.indexOf(document.activeElement as HTMLButtonElement);
  let next = 0;
  switch (key) {
    case "End": next = items.length - 1; break;
    case "ArrowDown": next = (index + 1) % items.length; break;
    case "ArrowUp": next = index <= 0 ? items.length - 1 : index - 1; break;
  }
  items[next].focus({ preventScroll: true });
}
