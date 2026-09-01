const fs = require("node:fs");
const fsp = require("node:fs/promises");
const path = require("node:path");
const { execFile } = require("node:child_process");
const { promisify } = require("node:util");

const execFileAsync = promisify(execFile);
const DAY_MS = 24 * 60 * 60 * 1000;
const MAX_AUTOMATIC_BACKUPS = 14;

function timestamp() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

class DatabaseBackups {
  constructor(paths, postgres) {
    this.paths = paths;
    this.postgres = postgres;
    this.timer = null;
    this.running = null;
    this.stopped = true;
  }

  environment() {
    return { ...process.env, PGPASSWORD: this.postgres.password };
  }

  connectionArguments(database = "wecanfindintern") {
    return [
      "-h", "127.0.0.1", "-p", String(this.postgres.port),
      "-U", "wecanfindintern", "-d", database,
    ];
  }

  async create(label = "manual") {
    if (this.running) return this.running;
    this.running = this.createUnsafe(label).finally(() => { this.running = null; });
    return this.running;
  }

  async createUnsafe(label) {
    await fsp.mkdir(this.paths.backups, { recursive: true, mode: 0o700 });
    if (process.platform !== "win32") await fsp.chmod(this.paths.backups, 0o700);
    const safeLabel = label.replace(/[^a-z0-9_-]/gi, "-").toLowerCase();
    const finalPath = path.join(this.paths.backups, `${timestamp()}-${safeLabel}.dump`);
    const temporaryPath = `${finalPath}.tmp`;
    try {
      await execFileAsync(this.postgres.bin("pg_dump"), [
        ...this.connectionArguments(),
        "--format=custom", "--compress=6", "--no-owner", "--no-privileges",
        "--file", temporaryPath,
      ], { env: this.environment(), timeout: 10 * 60 * 1000 });
      await fsp.rename(temporaryPath, finalPath);
      if (process.platform !== "win32") await fsp.chmod(finalPath, 0o600);
      await this.pruneAutomaticBackups();
      return finalPath;
    } catch (error) {
      await fsp.rm(temporaryPath, { force: true });
      throw error;
    }
  }

  async list() {
    await fsp.mkdir(this.paths.backups, { recursive: true, mode: 0o700 });
    if (process.platform !== "win32") await fsp.chmod(this.paths.backups, 0o700);
    const names = (await fsp.readdir(this.paths.backups))
      .filter((name) => name.endsWith(".dump"))
      .sort()
      .reverse();
    if (process.platform !== "win32") {
      await Promise.all(names.map((name) =>
        fsp.chmod(path.join(this.paths.backups, name), 0o600)));
    }
    return Promise.all(names.map(async (name) => {
      const filePath = path.join(this.paths.backups, name);
      const stats = await fsp.stat(filePath);
      return { name, path: filePath, size: stats.size, modifiedAt: stats.mtime.toISOString() };
    }));
  }

  async pruneAutomaticBackups() {
    const automatic = (await this.list())
      .filter((entry) => entry.name.endsWith("-automatic.dump"));
    await Promise.all(automatic.slice(MAX_AUTOMATIC_BACKUPS)
      .map((entry) => fsp.rm(entry.path, { force: true })));
  }

  async scheduleRestore(sourcePath) {
    const pendingPath = path.join(this.paths.runtime, "pending-restore.dump");
    await fsp.mkdir(this.paths.runtime, { recursive: true, mode: 0o700 });
    await fsp.copyFile(sourcePath, `${pendingPath}.tmp`);
    await fsp.rename(`${pendingPath}.tmp`, pendingPath);
  }

  async applyPendingRestore() {
    const pendingPath = path.join(this.paths.runtime, "pending-restore.dump");
    if (!fs.existsSync(pendingPath)) return false;
    const safetyBackup = await this.create("pre-restore");
    try {
      await this.restoreUnsafe(pendingPath);
      await fsp.rm(pendingPath, { force: true });
      return true;
    } catch (restoreError) {
      try {
        await this.restoreUnsafe(safetyBackup);
      } catch (rollbackError) {
        throw new AggregateError(
          [restoreError, rollbackError],
          "Database restore failed and the automatic rollback also failed.",
        );
      }
      await fsp.rename(
        pendingPath,
        path.join(this.paths.backups, `${timestamp()}-failed-restore.dump`),
      );
      throw restoreError;
    }
  }

  async restoreUnsafe(backupPath) {
    const common = [
      "-h", "127.0.0.1", "-p", String(this.postgres.port), "-U", "wecanfindintern",
    ];
    const options = { env: this.environment(), timeout: 15 * 60 * 1000 };
    await execFileAsync(this.postgres.bin("dropdb"), [
      ...common, "--if-exists", "--force", "wecanfindintern",
    ], options);
    await execFileAsync(this.postgres.bin("createdb"), [...common, "wecanfindintern"], options);
    await execFileAsync(this.postgres.bin("pg_restore"), [
      ...this.connectionArguments(),
      "--exit-on-error", "--no-owner", "--no-privileges", backupPath,
    ], options);
  }

  async start() {
    this.stopped = false;
    const backups = await this.list();
    const latestAutomatic = backups.find((entry) => entry.name.endsWith("-automatic.dump"));
    const age = latestAutomatic
      ? Math.max(0, Date.now() - Date.parse(latestAutomatic.modifiedAt))
      : DAY_MS;
    this.scheduleAutomatic(Math.max(0, DAY_MS - age));
  }

  scheduleAutomatic(delay) {
    this.timer = setTimeout(async () => {
      try {
        await this.create("automatic");
      } catch (error) {
        console.error("Automatic database backup failed", error);
      } finally {
        if (!this.stopped) this.scheduleAutomatic(DAY_MS);
      }
    }, delay);
    this.timer.unref();
  }

  async stop() {
    this.stopped = true;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    await this.running;
  }
}

module.exports = { DatabaseBackups };
