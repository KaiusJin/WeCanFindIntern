import { GlassButton, GlassInput } from "@zakisheriff/liquid-glass";
import { useId, useLayoutEffect, useState } from "react";
import { flushSync } from "react-dom";
import { Icon } from "../../layouts/AppChrome";
import { handleGlassMenuKey } from "../../components/glass-menu-keyboard";
import { SelectMenu } from "../../components/SelectMenu";

type Field = [name: string, label: string, type?: string];
type Entry = Record<string, string | string[] | null>;
type Chapter = {
  key: string;
  title: string;
  singular: string;
  fields: Field[];
  items: Entry[];
};
type Snapshot = { chapter: string; sections: Chapter[] };

function EntryField({ field: [name, label, type = "text"], entry }: { field: Field; entry: Entry }) {
  const initial = entry[name] ?? "";
  const [value, setValue] = useState(Array.isArray(initial) ? initial.join(type === "lines" ? "\n" : ", ") : initial);
  const attrs = { "data-profile-field": name, "data-profile-type": type, "aria-label": label };
  if (type === "language-level") {
    const levels = ["Beginner", "Intermediate", "Advanced", "Fluent", "Native"];
    if (value && !levels.includes(value)) levels.push(value);
    return <div className="profile-native-field" onKeyDown={handleGlassMenuKey}><span>{label}</span>
      <SelectMenu label={label} initial={value} width={200}
        choices={[{ value: "", label: "Select proficiency" }, ...levels.map(level => ({ value: level, label: level }))]}
        selectProps={{ "data-profile-field": name, "data-profile-type": type }} />
    </div>;
  }
  if (type === "textarea" || type === "lines") {
    return <label className="profile-native-field profile-item-wide"><span>{label}</span>
      <textarea className="career-textarea" rows={3} {...attrs} value={value} onChange={event => setValue(event.currentTarget.value)} />
    </label>;
  }
  return <GlassInput label={label} inputMode={type === "url" ? "url" : undefined} {...attrs}
    value={value} onChange={event => setValue(event.currentTarget.value)} />;
}

function SkillTags({ items }: { items: Entry[] }) {
  const listId = useId();
  const [skills, setSkills] = useState(() => items.map((entry, index) => ({
    key: String(entry.editorKey || entry.id || `${listId}-${index}`),
    id: entry.id ? String(entry.id) : undefined,
    name: String(entry.name ?? ""),
  })));
  return <>
    <header className="profile-chapter-head">
      <h2>Skills <small>{skills.filter(skill => skill.name.trim()).length}</small></h2>
    </header>
    <ul className="profile-skill-list" aria-label="Profile skills">
      {skills.map((skill, index) => (
        <li key={skill.key} className="profile-skill-tag" data-profile-section="skills"
          data-profile-index={index} data-profile-skill-key={skill.key} data-profile-skill-id={skill.id}>
          <input type="text" aria-label={`Skill name ${index + 1}`} title={skill.name || undefined}
            placeholder="Skill name" size={Math.max(1, (skill.name || "Skill name").length)}
            spellCheck={false} data-profile-field="name"
            value={skill.name} onChange={event => {
              const name = event.currentTarget.value;
              setSkills(current => current.map((item, i) => i === index ? { ...item, name } : item));
            }}
            onKeyDown={event => {
              if (event.key === "Enter" && !event.nativeEvent.isComposing) {
                event.preventDefault();
                event.currentTarget.closest("ul")?.querySelector<HTMLButtonElement>("[data-profile-add]")?.focus();
              }
            }} />
          <button type="button" className="profile-remove-item" aria-label={`Remove ${skill.name.trim() || "skill"}`}
            title={`Remove ${skill.name.trim() || "skill"}`}><Icon name="close" /></button>
        </li>
      ))}
      <li><button type="button" className="profile-skill-add" data-profile-add="skills">
        <Icon name="plus" />Add skill
      </button></li>
    </ul>
  </>;
}

function EntryList({ section }: { section: Chapter }) {
  const { key, title, singular, fields, items } = section;
  return <>
    <header className="profile-chapter-head">
      <h2>{title} <small>{items.length}</small></h2>
      <GlassButton size="sm" icon={<Icon name="plus" />} data-profile-add={key}>Add {singular}</GlassButton>
    </header>
    <div className="profile-section-body">
      <div className="profile-items">
        {items.map((entry, index) => {
          const entryName = String(entry[fields[0][0]] || title);
          const removeLabel = `Remove ${entry[fields[0][0]] || singular}`;
          const remove = <GlassButton className="profile-remove-item" size="sm" icon={<Icon name="trash" />}
            aria-label={removeLabel} title={removeLabel} />;
          return <article key={index} className="profile-item-card"
            data-profile-section={key} data-profile-index={index}>
            <div className="profile-item-head"><strong>{entryName}</strong>{remove}</div>
            <div className="profile-item-grid">{fields.map(field => <EntryField key={field[0]} field={field} entry={entry} />)}</div>
          </article>;
        })}
      </div>
      {!items.length && <p className="profile-chapter-empty">No {title.toLowerCase()} added yet.</p>}
    </div>
  </>;
}

export function ProfileEntries() {
  const [snapshot, setSnapshot] = useState<{ data: Snapshot; version: number } | null>(null);
  useLayoutEffect(() => {
    const host = document.getElementById("profile-repeat-sections")!;
    function replace(event: Event) {
      const data = (event as CustomEvent<Snapshot>).detail;
      // Replacing a complete form resets local input state before the controller validates or moves focus.
      flushSync(() => setSnapshot(previous => ({ data, version: (previous?.version ?? 0) + 1 })));
    }
    host.addEventListener("profile:replace-entries", replace);
    return () => host.removeEventListener("profile:replace-entries", replace);
  }, []);
  return snapshot?.data.sections.map(section => (
    <section key={`${snapshot.version}-${section.key}`} id={`profile-panel-${section.key}`}
      className={`profile-chapter-panel profile-section-${section.key}`} data-section={section.key}
      role="tabpanel" aria-labelledby={`profile-tab-${section.key}`} hidden={snapshot.data.chapter !== section.key}>
      {section.key === "skills" ? <SkillTags items={section.items} /> : <EntryList section={section} />}
    </section>
  ));
}
