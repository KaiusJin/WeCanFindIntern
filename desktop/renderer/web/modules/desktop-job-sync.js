const DEFAULT_POLL_INTERVAL_MS = 5000;

function jobSyncStatusView(sync) {
  if (!sync?.enabled) return { hidden: true, state: "disabled", text: "" };
  if (sync.running) {
    return { hidden: false, state: "running", text: "Syncing public jobs…" };
  }

  const result = sync.last_result;
  if (!result) return { hidden: true, state: "idle", text: "" };
  if (result.status === "success") {
    return { hidden: false, state: "success", text: `Public jobs up to date · v${result.version}` };
  }
  const detail = result.error_summary || sync.last_error || "Unknown sync error";
  return { hidden: true, state: "failed", text: "", title: detail };
}

function renderJobSyncStatus(sync) {
  const element = document.querySelector("#job-sync-status-text");
  if (!element) return;
  const view = jobSyncStatusView(sync);
  element.hidden = view.hidden;
  element.dataset.state = view.state;
  element.textContent = view.text;
  element.title = view.title || view.text;
}

function startDesktopJobSyncMonitor({
  refreshJobs,
  reportFailure,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
} = {}) {
  const desktop = window.weCanFindInternDesktop;
  if (!desktop?.getSyncStatus) return () => {};

  let previousRunning = null;
  let lastFinishedAt = null;
  let polling = false;
  let stopped = false;

  async function poll() {
    if (polling || stopped || document.visibilityState === "hidden") return;
    polling = true;
    try {
      const payload = await desktop.getSyncStatus();
      const sync = payload?.sync;
      if (!sync) return;
      renderJobSyncStatus(sync);

      const finishedAt = sync.last_finished_at || null;
      const completedTransition = previousRunning === true && sync.running === false;
      const completedWhileHidden = Boolean(
        previousRunning !== null && finishedAt && finishedAt !== lastFinishedAt,
      );
      previousRunning = sync.running;
      lastFinishedAt = finishedAt || lastFinishedAt;

      if (completedTransition || completedWhileHidden) {
        const result = jobSyncStatusView(sync);
        if (result.state === "failed") reportFailure?.(result.title);
        else if (typeof refreshJobs === "function") await refreshJobs();
      }
    } catch (error) {
      console.warn("Desktop job sync status unavailable:", error);
    } finally {
      polling = false;
    }
  }

  const timer = window.setInterval(poll, pollIntervalMs);
  const onVisibilityChange = () => {
    if (document.visibilityState === "visible") poll();
  };
  document.addEventListener("visibilitychange", onVisibilityChange);
  poll();

  return () => {
    stopped = true;
    window.clearInterval(timer);
    document.removeEventListener("visibilitychange", onVisibilityChange);
  };
}

export { jobSyncStatusView, startDesktopJobSyncMonitor };
