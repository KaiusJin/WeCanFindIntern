const fs = require("node:fs");
const fsp = require("node:fs/promises");
const path = require("node:path");
const readline = require("node:readline");
const { spawn } = require("node:child_process");

class BackendSidecar {
  constructor(app, paths, databaseUrl, token) {
    this.app = app;
    this.paths = paths;
    this.databaseUrl = databaseUrl;
    this.token = token;
    this.process = null;
    this.origin = null;
  }

  command() {
    if (this.app.isPackaged) {
      const name = process.platform === "win32" ? "wecanfindintern-backend.exe" : "wecanfindintern-backend";
      return {
        executable: path.join(this.paths.backendBundle, "wecanfindintern-backend", name),
        args: [],
      };
    }
    const executable = process.platform === "win32"
      ? path.join(this.paths.projectRoot, ".venv", "Scripts", "python.exe")
      : path.join(this.paths.projectRoot, ".venv", "bin", "python");
    return { executable, args: ["-m", "wecanfindintern.desktop.server"] };
  }

  async start() {
    const command = this.command();
    if (!fs.existsSync(command.executable)) {
      throw new Error(`Packaged Python backend is missing: ${command.executable}`);
    }
    await fsp.mkdir(this.paths.logs, { recursive: true });
    const stdoutLog = fs.createWriteStream(path.join(this.paths.logs, "backend-stdout.log"), { flags: "a" });
    const stderrLog = fs.createWriteStream(path.join(this.paths.logs, "backend-stderr.log"), { flags: "a" });
    const environment = {
      ...process.env,
      DATABASE_URL: this.databaseUrl,
      WCFI_DESKTOP_TOKEN: this.token,
      WCFI_USER_DATA_DIR: this.paths.userData,
      WCFI_RESOURCE_DIR: this.paths.resources,
      WCFI_BACKGROUND_COLLECTION_ENABLED: "1",
      PYTHONUNBUFFERED: "1",
    };
    if (!this.app.isPackaged) environment.PYTHONPATH = path.join(this.paths.projectRoot, "src");
    this.process = spawn(command.executable, command.args, {
      cwd: this.app.isPackaged ? this.paths.resources : this.paths.projectRoot,
      env: environment,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    this.process.stdout.pipe(stdoutLog);
    this.process.stderr.pipe(stderrLog);
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("Python backend startup timed out.")), 60000);
      const lines = readline.createInterface({ input: this.process.stdout });
      const fail = (error) => {
        clearTimeout(timeout);
        lines.close();
        reject(error);
      };
      this.process.once("error", fail);
      this.process.once("exit", (code) => {
        if (!this.origin) fail(new Error(`Python backend exited during startup (${code}).`));
      });
      lines.on("line", (line) => {
        try {
          const message = JSON.parse(line);
          if (message.type !== "ready") return;
          this.origin = `http://${message.host}:${message.port}`;
          clearTimeout(timeout);
          lines.close();
          resolve(this.origin);
        } catch (_) {
          // Ordinary application output remains in the sidecar log.
        }
      });
    });
  }

  async stop() {
    if (!this.process || this.process.exitCode !== null) return;
    this.process.kill("SIGTERM");
    await Promise.race([
      new Promise((resolve) => this.process.once("exit", resolve)),
      new Promise((resolve) => setTimeout(resolve, 8000)),
    ]);
    if (this.process.exitCode === null) this.process.kill("SIGKILL");
  }
}

module.exports = { BackendSidecar };
