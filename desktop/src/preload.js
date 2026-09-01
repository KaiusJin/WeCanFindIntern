const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("weCanFindInternDesktop", {
  getVersion: () => ipcRenderer.invoke("desktop:get-version"),
  getCollectionStatus: () => ipcRenderer.invoke("desktop:get-collection-status"),
  runCollectionNow: () => ipcRenderer.invoke("desktop:run-collection"),
  listBackups: () => ipcRenderer.invoke("desktop:list-backups"),
  createBackup: () => ipcRenderer.invoke("desktop:create-backup"),
  restoreBackup: () => ipcRenderer.invoke("desktop:restore-backup"),
  getAiSecrets: () => ipcRenderer.invoke("desktop:get-ai-secrets"),
  setAiSecrets: (values) => ipcRenderer.invoke("desktop:set-ai-secrets", values),
});
