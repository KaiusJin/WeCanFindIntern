const path = require("node:path");

function desktopPaths(app) {
  const projectRoot = path.resolve(__dirname, "../..");
  const userData = app.getPath("userData");
  const target = `${process.platform}-${process.arch}`;
  const resources = app.isPackaged ? process.resourcesPath : projectRoot;
  const nativeRoot = app.isPackaged ? process.resourcesPath : path.join(projectRoot, "desktop", "resources");
  return {
    projectRoot,
    userData,
    resources,
    runtime: path.join(userData, "runtime"),
    logs: path.join(userData, "logs"),
    backups: path.join(userData, "backups"),
    postgresData: path.join(userData, "postgres", "data"),
    postgresBundle: path.join(nativeRoot, "postgres", target),
    backendBundle: path.join(nativeRoot, "backend", target),
  };
}

module.exports = { desktopPaths };
