// Apply the saved appearance before paint, including when the desktop app is offline.
(() => {
  const key = "wcfi-appearance";
  const system = matchMedia("(prefers-color-scheme: dark)");
  const modes = ["system", "light", "dark"];
  const desktop = window.weCanFindInternDesktop;
  if (desktop?.windowStyle?.nativeBackdrop) document.documentElement.classList.add("native-backdrop");
  const titleBar = desktop?.windowStyle?.titleBar;
  if (titleBar === "inset" || titleBar === "overlay") document.documentElement.classList.add(titleBar + "-titlebar");
  let mode = desktop?.windowStyle?.theme || "system";
  try {
    const saved = (desktop?.windowStyle ? sessionStorage : localStorage).getItem(key);
    if (modes.includes(saved)) mode = saved;
  } catch { /* Storage can be disabled in private browsing. */ }

  function apply() {
    const dark = mode === "dark" || (mode === "system" && system.matches);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    document.documentElement.dataset.appearance = mode;
  }
  apply();
  system.addEventListener("change", apply);
  document.addEventListener("wcfi:appearance", event => {
    if (!modes.includes(event.detail)) return;
    mode = event.detail;
    apply();
    desktop?.setWindowStyle?.("theme", mode).catch(error => console.warn("Window theme could not be saved.", error));
    try { (desktop?.windowStyle ? sessionStorage : localStorage).setItem(key, mode); } catch { /* Keep the in-session choice. */ }
  });
})();
