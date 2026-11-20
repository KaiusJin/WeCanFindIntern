import { GlassButton, GlassInput } from "@zakisheriff/liquid-glass";
import { useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { Icon } from "../../layouts/AppChrome";

export function PostingSearchControls() {
  const [query, setQuery] = useState("");
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const form = input.current?.form;
    // The filters controller reads the form immediately after reset().
    const clearQuery = () => flushSync(() => setQuery(""));
    form?.addEventListener("reset", clearQuery);
    return () => form?.removeEventListener("reset", clearQuery);
  }, []);

  return (
    <>
      <div className="posting-query">
        <GlassInput
          ref={input}
          id="query"
          name="query"
          type="search"
          placeholder="Role, skill, or company"
          aria-label="Keywords, skills, or company"
          autoComplete="off"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          icon={<span aria-hidden="true" />}
          tint="var(--focus)"
        />
        <button type="submit" className="posting-query-submit" aria-label="Search public jobs" title="Search">
          <Icon name="search" />
        </button>
      </div>
      <GlassButton
        id="open-job-filters"
        popoverTarget="job-filters-sheet"
        className="posting-filter-button"
        icon={<Icon name="sliders" />}
        type="button"
        aria-haspopup="dialog"
        aria-controls="job-filters-sheet"
        aria-expanded="false"
      >
        <span id="posting-filter-label">Filters</span>
      </GlassButton>
    </>
  );
}
