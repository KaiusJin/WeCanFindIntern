const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const themes = ["system", "light", "dark"];
const preferences = { theme: "system" };

function loadWindowStyle(userData) {
  try {
    const saved = JSON.parse(fs.readFileSync(path.join(userData, "window-style.json"), "utf8"));
    if (themes.includes(saved?.theme)) preferences.theme = saved.theme;
  } catch (error) {
    if (error.code !== "ENOENT") console.warn("Window appearance preferences could not be read.");
  }
  return preferences;
}

// The sidecar uses a fresh port on each launch. Store the theme with the desktop profile.
function saveWindowStyle(userData, key, value) {
  if (key !== "theme" || !themes.includes(value)) {
    throw new TypeError("Invalid window appearance preference.");
  }
  const updated = { ...preferences, [key]: value };
  const file = path.join(userData, "window-style.json");
  fs.writeFileSync(file + ".tmp", JSON.stringify(updated), { mode: 0o600 });
  fs.renameSync(file + ".tmp", file);
  Object.assign(preferences, updated);
}

function windowMaterial(platform = process.platform, release = os.release()) {
  if (platform === "darwin") return { vibrancy: "sidebar", visualEffectState: "followWindow" };
  if (platform === "win32" && Number(release.split(".")[2]) >= 22621) {
    return { backgroundMaterial: "mica" };
  }
  return {};
}

function windowTitleBar(platform, dark) {
  if (platform === "darwin") {
    return { titleBarStyle: "hiddenInset", trafficLightPosition: { x: 16, y: 21 } };
  }
  if (platform === "win32") {
    return {
      titleBarStyle: "hidden",
      titleBarOverlay: {
        height: 58,
        color: dark ? "#1a1c21" : "#f7f6f2",
        symbolColor: dark ? "#f1efe9" : "#242731",
      },
    };
  }
  return {};
}

module.exports = { loadWindowStyle, saveWindowStyle, windowMaterial, windowTitleBar };
