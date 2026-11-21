import { GlassButton, GlassInput } from "@zakisheriff/liquid-glass";
import { useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { Icon } from "../../layouts/AppChrome";

export function PostingSearchControls({ waterlooWorks = false }: { waterlooWorks?: boolean }) {
  const [query, setQuery] = useState("");
  const [location, setLocation] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const prefix = waterlooWorks ? "ww-" : "";
  const sheetId = prefix + "job-filters-sheet";

  useEffect(() => {
    const form = input.current?.form;
    // The filters controller reads the form immediately after reset().
    const reset = () => flushSync(() => {
      setQuery("");
      setLocation("");
    });
    form?.addEventListener("reset", reset);
    return () => form?.removeEventListener("reset", reset);
  }, []);

  return (
    <>
      <div className="posting-query">
        <GlassInput
          ref={input}
          id={waterlooWorks ? "ww-job-query" : "query"}
          name="query"
          type="search"
          placeholder={waterlooWorks ? "Title, organization, or Job ID" : "Role, skill, or company"}
          aria-label={waterlooWorks ? "Keywords or job ID" : "Keywords, skills, or company"}
          autoComplete="off"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          icon={<span aria-hidden="true" />}
          tint="var(--focus)"
        />
        <button type="submit" className="posting-query-submit"
          aria-label={waterlooWorks ? "Search WaterlooWorks" : "Search public jobs"} title="Search">
          <Icon name="search" />
        </button>
      </div>
      {waterlooWorks && (
        <GlassInput id="ww-job-location" className="posting-location" name="location" type="search"
          autoComplete="off" aria-label="Location" placeholder="City or region" icon={<Icon name="pin" />}
          value={location} onChange={event => setLocation(event.target.value)} tint="var(--focus)" />
      )}
      <GlassButton
        id={prefix + "open-job-filters"}
        popoverTarget={sheetId}
        className="posting-filter-button"
        icon={<Icon name="sliders" />}
        type="button"
        aria-haspopup="dialog"
        aria-controls={sheetId}
        aria-expanded="false"
      >
        <span id={waterlooWorks ? "ww-filter-trigger-label" : "posting-filter-label"}>Filters</span>
      </GlassButton>
    </>
  );
}
