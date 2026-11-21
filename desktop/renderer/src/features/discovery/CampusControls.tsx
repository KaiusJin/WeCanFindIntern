import { GlassButton } from "@zakisheriff/liquid-glass";

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
