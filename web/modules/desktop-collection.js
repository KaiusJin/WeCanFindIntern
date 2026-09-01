const DEFAULT_POLL_INTERVAL_MS = 5000;

function number(value) {
  return Number(value || 0).toLocaleString("en-US");
}

function collectionStatusView(collection) {
  if (!collection?.enabled) return { hidden: true, state: "disabled", text: "" };
  if (collection.running) {
    return { hidden: false, state: "running", text: "Collecting new jobs…" };
  }

  const result = collection.last_result;
  if (!result) return { hidden: true, state: "idle", text: "" };
  const created = number(result.database_stats?.created);
  const failed = number(result.query_stats?.failed);
  const skipped = number(result.query_stats?.skipped);

  if (result.status === "success") {
    return { hidden: false, state: "success", text: `Last sync added ${created} jobs` };
  }
  if (result.status === "partial") {
    const unavailable = Number(result.query_stats?.failed || 0)
      + Number(result.query_stats?.skipped || 0);
    return {
      hidden: false,
      state: "partial",
      text: `Last sync added ${created} jobs · ${number(unavailable)} queries unavailable`,
      title: `${failed} failed, ${skipped} skipped after a provider circuit opened`,
    };
  }
  const detail = result.error_summary || collection.last_error || "Unknown collection error";
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
        lastFinishedAt && finishedAt && finishedAt !== lastFinishedAt,
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
