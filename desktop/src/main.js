const crypto = require("node:crypto");
const { app, BrowserWindow, dialog, ipcMain, Menu, nativeImage, nativeTheme, session, shell, Tray } = require("electron");
const { BackendSidecar } = require("./backend");
const { DatabaseBackups } = require("./backups");
const { desktopPaths } = require("./paths");
const { EmbeddedPostgres } = require("./postgres");
const { getOrCreateSecret, getSecrets, setSecrets } = require("./secrets");

const { loadWindowStyle, saveWindowStyle, windowMaterial, windowTitleBar } = require("./window-style");

const AI_SECRET_NAMES = ["deepseekKey", "geminiKey", "openaiKey", "glmKey", "qwenKey"];

if (require("electron-squirrel-startup")) app.quit();
if (process.platform === "win32") {
  app.setAppUserModelId("com.squirrel.WeCanFindIntern.WeCanFindIntern");
}
app.enableSandbox();

let backend;
let backups;
let postgres;
let mainWindow;
let tray;
let appOrigin;
let desktopToken;
let runtimePaths;
let quitting = false;
let shutdownStarted = false;
let shutdownPromise;
let restoreSelection;

function trustedExternalUrl(rawUrl) {
  try {
    const url = new URL(rawUrl);
    return url.protocol === "https:" || url.protocol === "mailto:";
  } catch (_) {
    return false;
  }
}

function configureSession(origin, token) {
  const filter = { urls: [`${origin}/*`] };
  session.defaultSession.webRequest.onBeforeSendHeaders(filter, (details, callback) => {
    details.requestHeaders["X-WeCanFindIntern-Token"] = token;
    callback({ requestHeaders: details.requestHeaders });
  });
  const allowsAudio = (permission, requestingOrigin, details = {}) => {
    const mediaTypes = Array.isArray(details.mediaTypes)
      ? details.mediaTypes
      : [details.mediaType].filter(Boolean);
    return permission === "media"
      && requestingOrigin === origin
      && mediaTypes.length > 0
      && mediaTypes.every((mediaType) => mediaType === "audio");
  };
  session.defaultSession.setPermissionCheckHandler(
    (_webContents, permission, requestingOrigin, details) =>
      allowsAudio(permission, requestingOrigin, details),
  );
  session.defaultSession.setPermissionRequestHandler(
    (webContents, permission, callback, details) => {
      const requestingOrigin = new URL(webContents.getURL()).origin;
      callback(allowsAudio(permission, requestingOrigin, details));
    },
  );
}

function createWindow(show) {
  const style = loadWindowStyle(runtimePaths.userData);
  nativeTheme.themeSource = style.theme;
  const material = windowMaterial();
  const nativeBackdrop = Boolean(material.vibrancy || material.backgroundMaterial);
  mainWindow = new BrowserWindow({
    ...material,
    ...windowTitleBar(process.platform, nativeTheme.shouldUseDarkColors),
    width: 1440,
    height: 940,
    minWidth: 1050,
    minHeight: 700,
    show: false,
    backgroundColor: nativeBackdrop ? "#00000000" : nativeTheme.shouldUseDarkColors ? "#1a1c21" : "#f7f6f2",
    webPreferences: {
      preload: require("node:path").join(__dirname, "preload.js"),
      additionalArguments: [
        `--wcfi-theme=${style.theme}`,
        ...(nativeBackdrop ? ["--wcfi-native-backdrop"] : []),
      ],
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
    },
  });
  if (process.platform === "win32") {
    const updateTitleBar = () => mainWindow.setTitleBarOverlay(windowTitleBar("win32", nativeTheme.shouldUseDarkColors).titleBarOverlay);
    nativeTheme.on("updated", updateTitleBar);
    mainWindow.once("closed", () => nativeTheme.off("updated", updateTitleBar));
  }
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (trustedExternalUrl(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (url === appOrigin || url.startsWith(`${appOrigin}/`)) return;
    event.preventDefault();
    if (trustedExternalUrl(url)) shell.openExternal(url);
  });
  mainWindow.on("close", (event) => {
    if (!quitting) {
      event.preventDefault();
      mainWindow.hide();
    }
  });
  mainWindow.once("ready-to-show", () => {
    if (show) mainWindow.show();
  });
  mainWindow.loadURL(appOrigin);
}

function trayImage() {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20"><rect x="2" y="3" width="16" height="14" rx="3" fill="#111"/><path d="M5 7h10M7 11h6M8 14h4" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/></svg>`;
  const image = nativeImage.createFromDataURL(`data:image/svg+xml;base64,${Buffer.from(svg).toString("base64")}`);
  if (process.platform === "darwin") image.setTemplateImage(true);
  return image;
}

async function requestJobSync(path, options = {}) {
  const response = await fetch(`${appOrigin}${path}`, {
    ...options,
    headers: { ...options.headers, "X-WeCanFindIntern-Token": desktopToken },
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || "Desktop API request failed.");
  return payload;
}

function trayError(title) {
  return error => {
    console.error(error);
    mainWindow.show();
    mainWindow.webContents.send("desktop:operation-error", title, error.message);
  };
}

function createTray() {
  tray = new Tray(trayImage());
  tray.setToolTip("WeCanFindIntern — public job sync");
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "Open WeCanFindIntern", click: () => mainWindow.show() },
    { label: "Sync public jobs now", click: () => requestJobSync("/desktop/sync/run", { method: "POST" }).catch(trayError("Job sync failed")) },
    { label: "Back up database now", click: () => backups.create("manual").catch(trayError("Backup failed")) },
    { label: "Restore database backup…", click: () => chooseRestoreBackup().catch(trayError("Restore could not be scheduled")) },
    { type: "separator" },
    { label: "Quit", click: () => { quitting = true; app.quit(); } },
  ]));
  tray.on("double-click", () => mainWindow.show());
}

function confirmDatabaseRestore() {
  const contents = mainWindow.webContents;
  if (contents.isDestroyed() || contents.isLoadingMainFrame()) return Promise.resolve(false);
  const requestId = crypto.randomUUID();
  return new Promise(resolve => {
    function finish(approved) {
      ipcMain.off("desktop:restore-answer", answer);
      contents.off("render-process-gone", cancel);
      contents.off("destroyed", cancel);
      contents.off("did-start-navigation", navigating);
      resolve(approved);
    }
    function cancel() { finish(false); }
    function navigating(info) {
      if (info.isMainFrame && !info.isSameDocument) cancel();
    }
    function answer(event, id, approved) {
      if (event.sender === contents && event.senderFrame === contents.mainFrame && id === requestId) {
        finish(approved === true);
      }
    }
    ipcMain.on("desktop:restore-answer", answer);
    contents.once("destroyed", cancel);
    contents.once("render-process-gone", cancel);
    contents.on("did-start-navigation", navigating);
    mainWindow.show();
    contents.send("desktop:confirm-restore", requestId);
  });
}

function chooseRestoreBackup() {
  if (!restoreSelection) {
    restoreSelection = selectAndScheduleRestore().finally(() => { restoreSelection = null; });
  }
  return restoreSelection;
}

async function selectAndScheduleRestore() {
  const selection = await dialog.showOpenDialog(mainWindow, {
    title: "Restore WeCanFindIntern database backup",
    defaultPath: backups.paths.backups,
    properties: ["openFile"],
    filters: [{ name: "WeCanFindIntern database backup", extensions: ["dump"] }],
  });
  if (selection.canceled || selection.filePaths.length !== 1) return { scheduled: false };
  if (!await confirmDatabaseRestore()) return { scheduled: false };
  await backups.scheduleRestore(selection.filePaths[0]);
  setTimeout(() => {
    quitting = true;
    app.relaunch();
    app.quit();
  }, 100);
  return { scheduled: true };
}

async function startRuntime() {
  const paths = desktopPaths(app);
  runtimePaths = paths;
  const postgresPassword = await getOrCreateSecret(paths.userData, "postgresPassword");
  desktopToken = crypto.randomBytes(36).toString("base64url");
  postgres = new EmbeddedPostgres(paths, postgresPassword);
  const databaseUrl = await postgres.start();
  backups = new DatabaseBackups(paths, postgres);
  await backups.applyPendingRestore();
  backend = new BackendSidecar(app, paths, databaseUrl, desktopToken);
  appOrigin = await backend.start();
  await backups.start();
  configureSession(appOrigin, desktopToken);
  const background = process.argv.includes("--background");
  createWindow(!background);
  createTray();
  if (app.isPackaged) {
    app.setLoginItemSettings({
      openAtLogin: true,
      openAsHidden: true,
      args: ["--background"],
    });
  } else {
    app.setLoginItemSettings({ openAtLogin: false });
  }
}

function shutdown() {
  if (shutdownPromise) return shutdownPromise;
  shutdownStarted = true;
  shutdownPromise = (async () => {
    await backups?.stop();
    await backend?.stop();
    await postgres?.stop();
  })();
  return shutdownPromise;
}

function shutdownAndQuit() {
  quitting = true;
  shutdown().finally(() => app.quit());
}

ipcMain.handle("desktop:set-window-style", (_event, key, value) => {
  saveWindowStyle(runtimePaths.userData, key, value);
  if (key === "theme") nativeTheme.themeSource = value;
});
ipcMain.handle("desktop:get-version", () => app.getVersion());
ipcMain.handle("desktop:get-sync-status", () => requestJobSync("/desktop/status"));
ipcMain.handle("desktop:run-sync", () => requestJobSync("/desktop/sync/run", { method: "POST" }));
ipcMain.handle("desktop:list-backups", () => backups.list());
ipcMain.handle("desktop:create-backup", () => backups.create("manual"));
ipcMain.handle("desktop:restore-backup", () => chooseRestoreBackup());
ipcMain.handle("desktop:get-ai-secrets", () => getSecrets(runtimePaths.userData, AI_SECRET_NAMES));
ipcMain.handle("desktop:set-ai-secrets", (_event, values) => {
  const safeValues = Object.fromEntries(AI_SECRET_NAMES.map((name) => [
    name,
    typeof values?.[name] === "string" ? values[name].trim() : "",
  ]));
  return setSecrets(runtimePaths.userData, safeValues);
});

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
    }
  });
  app.whenReady().then(startRuntime).catch((error) => {
    console.error(error);
    dialog.showErrorBox(
      "WeCanFindIntern could not start",
      `${error.message}\n\nDiagnostic logs are stored in the app data logs folder.`,
    );
    quitting = true;
    app.quit();
  });
  app.on("activate", () => mainWindow?.show());
  app.on("before-quit", (event) => {
    if (shutdownStarted) return;
    event.preventDefault();
    shutdownAndQuit();
  });
}

// An updater, terminal, or session shutdown can terminate Electron without a
// normal menu-driven quit. Handle the catchable process signals so the backend
// and database have an opportunity to flush and release their runtime files.
for (const signal of ["SIGINT", "SIGHUP", "SIGTERM"]) {
  process.once(signal, shutdownAndQuit);
}
