import { GlassInput } from "@zakisheriff/liquid-glass";
import { useLayoutEffect, useState } from "react";
import { flushSync } from "react-dom";

const groups = [
  {
    title: "Contact details",
    fields: [
      ["profile-full-name", "Full name", "text", "name"],
      ["profile-preferred-name", "Preferred name", "text", "nickname"],
      ["profile-email", "Email", "email", "email"],
      ["profile-phone", "Phone", "tel", "tel"],
    ],
  },
  {
    title: "Location",
    className: "profile-address-fields",
    fields: [
      ["profile-city", "City", "text", "address-level2"],
      ["profile-region", "Region", "text", "address-level1"],
      ["profile-country", "Country", "text", "country-name"],
    ],
  },
  {
    title: "Links",
    className: "profile-link-fields",
    fields: [
      ["profile-linkedin", "LinkedIn", "url"],
      ["profile-github", "GitHub", "url"],
      ["profile-portfolio", "Portfolio", "url"],
    ],
  },
];

export function ProfileBasicsFields() {
  const [values, setValues] = useState<Record<string, string>>({});
  useLayoutEffect(() => {
    const fields = document.getElementById("profile-basics-fields")!;
    function replaceValues(event: Event) {
      // The profile controller reads these fields immediately after loading or discarding a draft.
      const next = (event as CustomEvent<Record<string, string>>).detail;
      flushSync(() => setValues(next));
    }
    fields.addEventListener("profile:replace-basics", replaceValues);
    return () => fields.removeEventListener("profile:replace-basics", replaceValues);
  }, []);
  return groups.map(({ title, className, fields }) => (
    <fieldset className="profile-fieldset" key={title}>
      <legend>{title}</legend>
      <div className={["profile-basic-grid", className].filter(Boolean).join(" ")}>
        {fields.map(([id, label, type, autoComplete]) => (
          <GlassInput
            key={id}
            id={id}
            label={label}
            aria-label={label}
            type={type === "url" ? "text" : type}
            inputMode={type === "url" ? "url" : undefined}
            autoComplete={autoComplete}
            value={values[id] ?? ""}
            onChange={(event) => {
              const value = event.currentTarget.value;
              setValues((previous) => ({ ...previous, [id]: value }));
            }}
          />
        ))}
      </div>
    </fieldset>
  ));
}
