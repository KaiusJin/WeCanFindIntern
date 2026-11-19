const DEFAULT_POLL_INTERVAL_MS = 5000;

function collectionStatusView(collection) {
  if (!collection?.enabled) return { hidden: true, state: "disabled", text: "" };
  if (collection.running) {
    return { hidden: false, state: "running", text: "Syncing public jobs…" };
  }

  const result = collection.last_result;
  if (!result) return { hidden: true, state: "idle", text: "" };
  if (result.status === "success") {
    return { hidden: false, state: "success", text: `Public jobs up to date · v${result.version}` };
  }
  const detail = result.error_summary || collection.last_error || "Unknown sync error";
  return { hidden: false, state: "failed", text: "Last job sync failed", title: detail };
}

function renderCollectionStatus(collection) {
  const element = document.querySelector("#collection-status-text");
  if (!element) return;
  const view = collectionStatusView(collection);
  element.hidden = view.hidden;
  element.dataset.state = view.state;
  element.textContent = view.text;
  element.title = view.title || view.text;
}

function startDesktopCollectionMonitor({
  refreshJobs,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
} = {}) {
  const desktop = window.weCanFindInternDesktop;
  if (!desktop?.getCollectionStatus) return () => {};

  let previousRunning = null;
  let lastFinishedAt = null;
  let polling = false;
  let stopped = false;

  async function poll() {
    if (polling || stopped || document.visibilityState === "hidden") return;
    polling = true;
    try {
      const payload = await desktop.getCollectionStatus();
      const collection = payload?.collection;
      if (!collection) return;
      renderCollectionStatus(collection);

      const finishedAt = collection.last_finished_at || null;
      const completedTransition = previousRunning === true && collection.running === false;
      const completedWhileHidden = Boolean(
        previousRunning !== null && finishedAt && finishedAt !== lastFinishedAt,
      );
      previousRunning = collection.running;
      lastFinishedAt = finishedAt || lastFinishedAt;

      if ((completedTransition || completedWhileHidden) && typeof refreshJobs === "function") {
        await refreshJobs();
      }
    } catch (error) {
      console.warn("Desktop collection status unavailable:", error);
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

export { collectionStatusView, startDesktopCollectionMonitor };
