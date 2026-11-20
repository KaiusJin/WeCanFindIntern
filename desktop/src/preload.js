const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("weCanFindInternDesktop", {
  windowStyle: {
    titleBar: process.platform === "darwin" ? "inset" : process.platform === "win32" ? "overlay" : "system",
    theme: process.argv.find((arg) => arg.startsWith("--wcfi-theme="))?.split("=")[1] || "system",
    nativeBackdrop: process.argv.includes("--wcfi-native-backdrop"),
  },
  setWindowStyle: (key, value) => ipcRenderer.invoke("desktop:set-window-style", key, value),
  getVersion: () => ipcRenderer.invoke("desktop:get-version"),
  getSyncStatus: () => ipcRenderer.invoke("desktop:get-sync-status"),
  syncJobsNow: () => ipcRenderer.invoke("desktop:run-sync"),
  listBackups: () => ipcRenderer.invoke("desktop:list-backups"),
  createBackup: () => ipcRenderer.invoke("desktop:create-backup"),
  restoreBackup: () => ipcRenderer.invoke("desktop:restore-backup"),
  onOperationError: (notify) => {
    const handle = (_event, title, message) => notify(title, message);
    ipcRenderer.on("desktop:operation-error", handle);
    return () => ipcRenderer.off("desktop:operation-error", handle);
  },
  onRestoreConfirmation: (confirm) => {
    const handle = async (_event, id) => {
      let approved = false;
      try { approved = await confirm() === true; }
      catch { /* A missing or closed confirmation always cancels the restore. */ }
      ipcRenderer.send("desktop:restore-answer", id, approved);
    };
    ipcRenderer.on("desktop:confirm-restore", handle);
    return () => ipcRenderer.off("desktop:confirm-restore", handle);
  },
  getAiSecrets: () => ipcRenderer.invoke("desktop:get-ai-secrets"),
  setAiSecrets: (values) => ipcRenderer.invoke("desktop:set-ai-secrets", values),
});
