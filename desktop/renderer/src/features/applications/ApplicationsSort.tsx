import { GlassMenu } from "@zakisheriff/liquid-glass";
import { useRef, useState } from "react";
import { focusGlassMenuTrigger, handleGlassMenuKey } from "../../components/glass-menu-keyboard";
import { Icon } from "../../layouts/AppChrome";

const options = [
  { value: "updated:desc", label: "Updated" },
  { value: "applied:desc", label: "Date applied" },
  { value: "company:asc", label: "Company A–Z" },
];

export function ApplicationsSort() {
  const select = useRef<HTMLSelectElement>(null);
  const [value, setValue] = useState(options[0].value);
  const label = options.find((option) => option.value === value)?.label ?? options[0].label;

  function choose(next: string) {
    const control = select.current;
    if (!control) return;
    if (control.value !== next) {
      control.value = next;
      control.dispatchEvent(new Event("change", { bubbles: true }));
    }
    focusGlassMenuTrigger(control.parentElement);
  }

  return (
    <div className="tracker-sort-control" onKeyDown={handleGlassMenuKey}>
      <select id="tracker-sort" hidden defaultValue={options[0].value}
        onChange={(event) => setValue(event.currentTarget.value)} ref={select}>
        {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
      <GlassMenu
        className="tracker-sort-menu"
        size="sm"
        width={176}
        aria-label={`Sort applications by ${label}`}
        icon={<>{label}<Icon name="chevron-down" /></>}
        items={options.map((option) => ({
          label: option.label,
          checked: option.value === value,
          onSelect: () => choose(option.value),
        }))}
      />
    </div>
  );
}
