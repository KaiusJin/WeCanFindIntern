import { GlassButton, GlassInput } from "@zakisheriff/liquid-glass";
import { useLayoutEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { Icon } from "../../layouts/AppChrome";

export function CampusSearchControls() {
  const [query, setQuery] = useState("");
  const [location, setLocation] = useState("");
  const ref = useRef<HTMLInputElement>(null);
  useLayoutEffect(() => {
    const form = ref.current?.form;
    const reset = () => flushSync(() => { setQuery(""); setLocation(""); });
    form?.addEventListener("reset", reset);
    return () => form?.removeEventListener("reset", reset);
  }, []);
  return (
    <>
      <GlassInput ref={ref} id="ww-job-query" type="search" autoComplete="off" aria-label="Keywords or job ID"
        placeholder="Title, organization, or Job ID" value={query} onChange={event => setQuery(event.target.value)} />
      <GlassInput id="ww-job-location" type="search" autoComplete="off" aria-label="Location" placeholder="City or region"
        value={location} onChange={event => setLocation(event.target.value)} icon={<Icon name="pin" />} />
      <GlassButton id="ww-open-job-filters" type="button" size="sm" icon={<Icon name="sliders" />}
        popoverTarget="ww-job-filters-sheet" aria-controls="ww-job-filters-sheet" aria-haspopup="dialog" aria-expanded="false">
        <span id="ww-filter-trigger-label">Filters</span>
      </GlassButton>
      <GlassButton id="ww-job-search" size="sm" type="submit" variant="filled">Search</GlassButton>
    </>
  );
}

export function CampusConnection() {
  return <GlassButton id="ww-launch" type="button" size="sm"><span id="ww-launch-label">Log into WaterlooWorks ↗</span></GlassButton>;
}

export function CampusSyncActions() {
  return (
    <>
      <GlassButton id="ww-cancel-sync" popoverTarget="ww-sync-dialog" popoverTargetAction="hide" size="sm" type="button">Cancel</GlassButton>
      <GlassButton id="ww-start-sync" variant="filled" size="sm" type="button">Start sync</GlassButton>
    </>
  );
}
