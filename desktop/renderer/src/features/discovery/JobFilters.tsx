import { GlassButton, GlassCard, GlassInput, GlassMenu, GlassSegmented, GlassSwitch } from "@zakisheriff/liquid-glass";
import { useLayoutEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { handleGlassMenuKey, focusGlassMenuTrigger } from "../../components/glass-menu-keyboard";
import { Icon } from "../../layouts/AppChrome";

type Board = { waterlooWorks?: boolean };

function Facet({ id, label, placeholder, inline = false }: {
  id: string; label: string; placeholder: string; inline?: boolean;
}) {
  return (
    <div id={id} className={inline ? "multi-filter facet-chips" : "multi-filter facet-picker"}
      role="group" aria-labelledby={id + "-label"} data-placeholder={placeholder}>
      <div className="facet-label-row">
        <span className="facet-label" id={id + "-label"}>{label}</span>
        <button className="multi-filter-clear" type="button" hidden aria-label={`Clear ${label.toLowerCase()} filter`}>Clear</button>
      </div>
      {!inline && (
        <GlassButton id={id + "-trigger"} className="multi-filter-trigger" type="button" size="sm"
          aria-expanded="false" aria-controls={id + "-options"} aria-labelledby={id + "-label " + id + "-value"}>
          <span id={id + "-value"} className="multi-filter-summary">{placeholder}</span>
          <Icon name="chevron-down" />
        </GlassButton>
      )}
      <div id={id + "-options"} className={inline ? "facet-options" : "multi-filter-menu"} hidden={!inline}>
        <div className="multi-filter-options" />
      </div>
    </div>
  );
}

const categories = [
  ["", "All categories"],
  ["software_engineering", "Software engineering"],
  ["data_ai", "Data & AI"],
  ["cybersecurity", "Cybersecurity"],
  ["cloud_devops", "Cloud & DevOps"],
  ["qa_testing", "QA & testing"],
  ["product_design", "Product design"],
  ["product_management", "Product management"],
  ["it_support", "IT support"],
  ["hardware_embedded", "Hardware & embedded"],
  ["research", "Research"],
  ["business_operations", "Business operations"],
  ["finance", "Finance"],
  ["marketing_sales", "Marketing & sales"],
  ["human_resources", "Human resources"],
  ["healthcare", "Healthcare"],
  ["education", "Education"],
  ["skilled_trades", "Skilled trades"],
  ["engineering", "Engineering"],
  ["architecture_planning", "Architecture & planning"],
  ["legal", "Legal"],
  ["customer_service", "Customer service"],
  ["supply_chain", "Supply chain"],
  ["administrative", "Administrative"],
  ["other", "Other"],
] as const;

function CategoryPicker({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const host = useRef<HTMLDivElement>(null);
  const name = categories.find(([key]) => key === value)?.[1] ?? "All categories";
  return (
    <div className="facet-category" ref={host} onKeyDown={event => {
      if (event.key === "Escape" && host.current?.querySelector('[aria-expanded="true"]')) event.preventDefault();
      handleGlassMenuKey(event);
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) && document.activeElement?.matches('[role="menuitemradio"]')) {
        document.activeElement.scrollIntoView({ block: "nearest" });
      }
    }}>
      <span className="facet-label">Job category</span>
      <input id="ww-filter-category" readOnly hidden value={value} />
      <GlassMenu align="start" size="sm" width={248} aria-label={`Job category, ${name}`}
        icon={<>{name}<Icon name="chevron-down" /></>}
        items={categories.map(([key, label]) => ({
          label, checked: value === key, onSelect: () => {
            flushSync(() => onChange(key));
            host.current?.querySelector("input")?.dispatchEvent(new Event("change", { bubbles: true }));
            focusGlassMenuTrigger(host.current);
          },
        }))}
      />
    </div>
  );
}

function HourlyPay() {
  const bounds = ["min", "max"] as const;
  return (
    <fieldset className="salary-filter-group">
      <legend className="field-label">Hourly pay range</legend>
      <div className="salary-range-inputs">
        {bounds.map(bound => {
          const label = bound === "min" ? "Minimum" : "Maximum";
          return (
            <div className="salary-bound" key={bound}>
              <label className="salary-bound-label" htmlFor={"hourly-" + bound}>{label}</label>
              <div className="salary-input-box" data-field-control>
                <span className="prefix">$</span>
                <input id={"hourly-" + bound} type="number" min="0" step="any"
                  placeholder={bound === "min" ? "0" : "No limit"} aria-label={label + " hourly rate"} />
                <span className="suffix">/hr</span>
              </div>
            </div>
          );
        })}
      </div>
      <div className="dual-slider-wrapper">
        <div className="slider-track-container">
          <div className="slider-track-base" />
          <div id="slider-track-fill" className="slider-track-fill" />
          {bounds.map(bound => (
            <input key={bound} id={"hourly-slider-" + bound} className={"dual-range-slider " + bound + "-slider"}
              type="range" min="0" max="100" step="1" defaultValue={bound === "min" ? "0" : "100"}
              aria-label={bound === "min" ? "Minimum hourly pay" : "Maximum hourly pay"} />
          ))}
        </div>
        <div className="slider-ticks" aria-hidden="true">
          {["$0", "$25", "$50", "$75", "$100+"].map(price => <span key={price}>{price}</span>)}
        </div>
      </div>
    </fieldset>
  );
}

const publicSections = [{ label: "Role", icon: <Icon name="briefcase" /> }, { label: "Location", icon: <Icon name="pin" /> }, { label: "Pay", icon: <Icon name="sliders" /> }];
const campusSections = [publicSections[0], publicSections[1], { label: "Source", icon: <Icon name="campus" /> }];

export function FilterControls({ waterlooWorks = false }: Board) {
  const prefix = waterlooWorks ? "ww-" : "";
  const sheetId = prefix + "job-filters-sheet";
  const sections = waterlooWorks ? campusSections : publicSections;
  const [section, setSection] = useState(0);
  const [values, setValues] = useState<Record<string, string>>({});
  const [salary, setSalary] = useState(false);
  const tabs = useRef<HTMLDivElement>(null);
  const body = useRef<HTMLDivElement>(null);

  // Validation can reveal a field from another group, including when the
  // main search is submitted with an invalid pay range.
  useLayoutEffect(() => {
    const sheet = document.getElementById(sheetId)!;
    tabs.current?.querySelectorAll('[role="tab"]').forEach((tab, i) => {
      tab.id = sheetId + "-tab-" + i;
      tab.setAttribute("aria-controls", sheetId + "-group-" + i);
    });
    const revealPay = () => flushSync(() => setSection(2));
    sheet.addEventListener("wcfi:show-final-filter-group", revealPay);
    return () => sheet.removeEventListener("wcfi:show-final-filter-group", revealPay);
  }, [sheetId]);

  function setField(id: string, value: string) {
    setValues(previous => ({ ...previous, [id]: value }));
  }
  function field(id: string, label: string, placeholder: string, type = "text") {
    return <GlassInput id={id} label={label} type={type} placeholder={placeholder} aria-label={label}
      autoComplete="off" value={values[id] ?? ""} onChange={event => setField(id, event.target.value)}
      tint="var(--accent)" />;
  }
  const modeId = waterlooWorks ? "ww-work-mode-filter" : "work-mode";
  const typeId = waterlooWorks ? "ww-opportunity-type-filter" : "opportunity-type";
  return (
    <>
      <header className="facet-panel-heading">
        <h2>Filter jobs</h2>
        <GlassButton id={prefix + "close-job-filters"} type="button" size="sm" icon={<Icon name="close" />}
          aria-label="Close filters" popoverTarget={sheetId} popoverTargetAction="hide" />
      </header>
      <div className="facet-tabs" ref={tabs}>
        <GlassSegmented aria-label="Filter groups" options={sections} value={section} size="sm"
          onChange={index => { setSection(index); body.current?.scrollTo({ top: 0 }); }} />
      </div>
      <div className="facet-panel-body" ref={body}>
        <section className="facet-grid" role="tabpanel" id={sheetId + "-group-0"} aria-labelledby={sheetId + "-tab-0"} hidden={section !== 0}>
          <Facet id={modeId} label="Work mode" placeholder="Any work mode" inline />
          <Facet id={typeId} label="Opportunity type" placeholder="All opportunity types" />
          {waterlooWorks ? (
            <>
              <CategoryPicker value={values.category ?? ""} onChange={value => setField("category", value)} />
              {field("ww-filter-company", "Company", "Company name")}
              {field("ww-filter-skill", "Skill", "e.g. Python")}
            </>
          ) : (
            <>
              <Facet id="recruiting-term" label="Recruiting term" placeholder="All recruiting seasons" />
              <Facet id="schedule-type" label="Schedule" placeholder="All schedules" />
              <Facet id="category" label="Job category" placeholder="All categories" />
              <Facet id="skill" label="Skill" placeholder="All skills" />
            </>
          )}
        </section>
        <section className="facet-grid" role="tabpanel" id={sheetId + "-group-1"} aria-labelledby={sheetId + "-tab-1"} hidden={section !== 1}>
          {waterlooWorks ? (
            <>
              {field("ww-filter-country", "Country", "e.g. Canada")}
              {field("ww-filter-region", "State or province", "e.g. Ontario")}
              {field("ww-filter-city", "City", "e.g. Waterloo")}
            </>
          ) : (
            <>
              <div className="facet-wide">{field("location", "Location keywords", "City or region", "search")}</div>
              <Facet id="country" label="Country" placeholder="All countries" />
              <Facet id="region" label="State or province" placeholder="All regions" />
              <Facet id="city" label="City" placeholder="All cities" />
            </>
          )}
        </section>
        <section className="facet-grid" role="tabpanel" id={sheetId + "-group-2"} aria-labelledby={sheetId + "-tab-2"} hidden={section !== 2}>
          {waterlooWorks ? (
            <>
              <Facet id="ww-board-source-filter" label="Board source" placeholder="All board sources" />
              {field("ww-filter-posted-after", "Posted after", "", "date")}
            </>
          ) : (
            <>
              <div className="facet-switch-row">
                <label htmlFor="has-salary">Only show jobs with salary</label>
                <GlassSwitch id="has-salary" aria-label="Only show jobs with salary" checked={salary} tint="var(--accent)"
                  onChange={value => {
                    flushSync(() => setSalary(value));
                    document.getElementById("has-salary")?.dispatchEvent(new Event("change", { bubbles: true }));
                  }} />
              </div>
              <HourlyPay />
            </>
          )}
        </section>
      </div>
      <footer className="facet-panel-footer">
        <GlassButton id={prefix + "clear-filters"} size="sm" type="button" onClickCapture={() =>
          flushSync(() => { setValues({}); setSalary(false); })}>Reset filters</GlassButton>
        <GlassButton id={prefix + "show-filter-results"} size="sm" type="button" variant="filled">
          <span id={prefix + "facet-results-label"}>Show results</span>
        </GlassButton>
      </footer>
    </>
  );
}

export function JobFilters({ waterlooWorks = false }: Board) {
  const prefix = waterlooWorks ? "ww-" : "";
  return (
    <GlassCard popover="auto" role="dialog" data-workspace-popover={waterlooWorks ? "tab-waterlooworks" : "tab-jobs"}
      id={prefix + "job-filters-sheet"} className="filters-panel filter-sheet" padding="0" radius="26px"
      aria-label={waterlooWorks ? "WaterlooWorks job filters" : "Public job filters"}>
      <div id={prefix + "filter-controls"} className="facet-panel">
        <FilterControls waterlooWorks={waterlooWorks} />
      </div>
    </GlassCard>
  );
}
