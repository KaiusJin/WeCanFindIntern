import { GlassButton, GlassMenu } from "@zakisheriff/liquid-glass";
import { useLayoutEffect, useRef, useState, type ReactNode, type SelectHTMLAttributes } from "react";
import { focusGlassMenuTrigger } from "./glass-menu-keyboard";
import { Icon } from "../layouts/AppChrome";

export type Choice = { value: string; label: string };

type Props = {
  label: string;
  /** Options; when omitted they are read from the `<option>` children. */
  choices?: Choice[];
  children?: ReactNode;
  initial?: string;
  /** Events, on the select or on its `syncHost` ancestor, after which a DOM controller changed the select. */
  syncOn?: string[];
  syncHost?: string;
  /** Leading content inside the trigger, such as a status dot. */
  leading?: ReactNode;
  className?: string;
  lockedClassName?: string;
  width?: number;
  align?: "start" | "end";
  hideChoice?: (choice: Choice) => boolean;
  /** Called whenever the select's value is read, including controller-driven fills. */
  onValue?: (value: string) => void;
  selectProps?: SelectHTMLAttributes<HTMLSelectElement> & Record<`data-${string}`, string>;
};

const readChoices = (field: HTMLSelectElement) => Array.from(field.options, option => ({ value: option.value, label: option.text }));

/**
 * Liquid Glass pull-down backed by a hidden native `<select>`. The controllers in
 * web/modules keep reading and listening to the select; this menu only mirrors it.
 * Render it inside the element that should receive focus back after a choice.
 */
export function SelectMenu({
  label, choices, children, initial, syncOn = [], syncHost, leading, className, lockedClassName,
  width = 176, align = "start", hideChoice, onValue, selectProps,
}: Props) {
  const select = useRef<HTMLSelectElement>(null);
  const notify = useRef(onValue);
  notify.current = onValue;
  const [options, setOptions] = useState<Choice[]>(choices ?? []);
  const [value, setValue] = useState(initial ?? choices?.[0]?.value ?? "");
  const [locked, setLocked] = useState(false);

  useLayoutEffect(() => {
    const field = select.current!;
    const host = syncHost ? field.closest(syncHost) : null;
    function read() {
      setOptions(readChoices(field));
      setValue(field.value);
      setLocked(field.disabled);
      notify.current?.(field.value);
    }
    read();
    const targets = [field, host].filter(Boolean) as Element[];
    const events = ["change", ...syncOn];
    targets.forEach(target => events.forEach(event => target.addEventListener(event, read)));
    return () => targets.forEach(target => events.forEach(event => target.removeEventListener(event, read)));
  }, []);

  function choose(next: string) {
    const field = select.current!;
    if (field.value !== next) {
      field.value = next;
      field.dispatchEvent(new Event("change", { bubbles: true }));
    }
    focusGlassMenuTrigger(field.parentElement);
  }

  const selected = options.find(option => option.value === value)?.label || value || label;
  return (
    <>
      <select {...selectProps} hidden ref={select} defaultValue={initial} aria-label={label}>
        {children ?? choices?.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
      {locked
        ? <GlassButton className={lockedClassName} size="sm" disabled aria-label={`${label}, ${selected}`}>{selected}</GlassButton>
        : <GlassMenu className={className} size="sm" width={width} align={align} aria-label={`${label}: ${selected}`}
          icon={<>{leading}<span>{selected}</span><Icon name="chevron-down" /></>}
          items={options.filter(option => !hideChoice?.(option)).map(option => ({
            label: option.label,
            checked: option.value === value,
            onSelect: () => choose(option.value),
          }))} />}
    </>
  );
}
