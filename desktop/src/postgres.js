const fs = require("node:fs");
const fsp = require("node:fs/promises");
const net = require("node:net");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { promisify } = require("node:util");
const { execFile } = require("node:child_process");

const execFileAsync = promisify(execFile);

function executableName(name) {
  return process.platform === "win32" ? `${name}.exe` : name;
}

async function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => resolve(address.port));
    });
  });
}

class EmbeddedPostgres {
  constructor(paths, password, options = {}) {
    this.paths = paths;
    this.password = password;
    this.execFile = options.execFile || execFileAsync;
    this.readFile = options.readFile || fsp.readFile;
    this.process = null;
    this.port = null;
    this.logFd = null;
  }

  bin(name) {
    return path.join(this.paths.postgresBundle, "bin", executableName(name));
  }

  async verifyBundle() {
    const required = ["postgres", "initdb", "pg_ctl", "pg_isready", "createdb", "psql"];
    const missing = required.filter((name) => !fs.existsSync(this.bin(name)));
    const vectorControls = [
      path.join(this.paths.postgresBundle, "share", "extension", "vector.control"),
      path.join(this.paths.postgresBundle, "share", "postgresql@16", "extension", "vector.control"),
    ];
    if (!vectorControls.some((candidate) => fs.existsSync(candidate))) missing.push("pgvector extension");
    if (missing.length) {
      throw new Error(`Embedded PostgreSQL bundle is incomplete: ${missing.join(", ")}`);
    }
  }

  async initialize() {
    const versionPath = path.join(this.paths.postgresData, "PG_VERSION");
    if (fs.existsSync(versionPath)) {
      const majorVersion = (await fsp.readFile(versionPath, "utf8")).trim();
      if (majorVersion !== "16") {
        throw new Error(
          `The local database uses PostgreSQL ${majorVersion}; this app requires PostgreSQL 16. `
          + "Restore a compatible backup or run the documented major-version upgrade first.",
        );
      }
      return false;
    }
    await fsp.mkdir(this.paths.runtime, { recursive: true });
    await fsp.mkdir(path.dirname(this.paths.postgresData), { recursive: true });
    const passwordFile = path.join(this.paths.runtime, "postgres-password.tmp");
    await fsp.writeFile(passwordFile, this.password, { mode: 0o600 });
    try {
      await execFileAsync(this.bin("initdb"), [
        "-D", this.paths.postgresData,
        "--username=wecanfindintern",
        "--encoding=UTF8",
        "--locale=C",
        "--auth-host=scram-sha-256",
        "--auth-local=scram-sha-256",
        `--pwfile=${passwordFile}`,
      ]);
    } finally {
      await fsp.rm(passwordFile, { force: true });
    }
    return true;
  }

  async previousServerIsRunning() {
    try {
      await this.execFile(this.bin("pg_ctl"), [
        "status", "-D", this.paths.postgresData,
      ], { timeout: 5000 });
      return true;
    } catch (error) {
      // pg_ctl documents exit status 3 as "server is not running". Other
      // failures (permissions, an unreadable data directory, or a timeout)
      // must not be mistaken for a stale lock file.
      if (error?.code === 3) return false;
      throw new Error(
        "Could not inspect the previous embedded PostgreSQL process.",
        { cause: error },
      );
    }
  }

  async previousServerDetails() {
    const pidPath = path.join(this.paths.postgresData, "postmaster.pid");
    let fields;
    try {
      fields = (await this.readFile(pidPath, "utf8")).split(/\r?\n/);
    } catch (error) {
      throw new Error(
        "The previous embedded PostgreSQL process is running, but its lock file could not be read.",
        { cause: error },
      );
    }
    const recordedData = path.resolve(fields[1] || "");
    const expectedData = path.resolve(this.paths.postgresData);
    const sameDataDirectory = process.platform === "win32"
      ? recordedData.toLowerCase() === expectedData.toLowerCase()
      : recordedData === expectedData;
    const port = Number.parseInt(fields[3], 10);
    if (!sameDataDirectory || !Number.isInteger(port) || port < 1 || port > 65535) {
      throw new Error(
        "The previous embedded PostgreSQL lock file does not match this app database.",
      );
    }
    return { port };
  }

  async verifyPreviousServer(details) {
    try {
      await this.execFile(this.bin("pg_isready"), [
        "-h", "127.0.0.1", "-p", String(details.port), "-U", "wecanfindintern",
      ], { timeout: 3000 });
    } catch (error) {
      throw new Error(
        "A previous process owns the app database lock, but it is not a responsive PostgreSQL server.",
        { cause: error },
      );
    }
  }

  async recoverPreviousServer() {
    if (!(await this.previousServerIsRunning())) return false;

    const details = await this.previousServerDetails();
    await this.verifyPreviousServer(details);

    // Electron owns this data directory and has already acquired its
    // single-instance lock. A server found here therefore belongs to an
    // earlier app process that exited before it could finish cleanup.
    console.warn("Stopping an orphaned embedded PostgreSQL process from a previous app run.");
    try {
      await this.execFile(this.bin("pg_ctl"), [
        "stop", "-D", this.paths.postgresData, "-m", "fast", "-w", "-t", "20",
      ], { timeout: 25000 });
    } catch (error) {
      // The previous server may have completed shutdown while pg_ctl was
      // reporting an error. Recheck before failing startup.
      if (!(await this.previousServerIsRunning())) return true;
      throw new Error(
        "A previous embedded PostgreSQL process is still using the app database and could not be stopped safely.",
        { cause: error },
      );
    }
    return true;
  }

  async start() {
    await this.verifyBundle();
    await this.initialize();
    await this.recoverPreviousServer();
    this.port = await freePort();
    await fsp.mkdir(this.paths.logs, { recursive: true });
    const socketDirectory = path.join(this.paths.runtime, "postgres-socket");
    await fsp.mkdir(socketDirectory, { recursive: true, mode: 0o700 });
    await fsp.chmod(socketDirectory, 0o700);
    this.logFd = fs.openSync(path.join(this.paths.logs, "postgres.log"), "a");
    const postgresArguments = [
      "-D", this.paths.postgresData,
      "-h", "127.0.0.1",
      "-p", String(this.port),
      "-c", "password_encryption=scram-sha-256",
    ];
    if (process.platform !== "win32") postgresArguments.push("-k", socketDirectory);
    this.process = spawn(this.bin("postgres"), postgresArguments,
      { stdio: ["ignore", this.logFd, this.logFd], windowsHide: true });

    this.process.once("exit", (code) => {
      if (code !== 0) console.error(`Embedded PostgreSQL exited with code ${code}`);
    });
    await this.waitUntilReady();
    await this.ensureApplicationDatabase();
    return this.databaseUrl();
  }

  async waitUntilReady() {
    const environment = { ...process.env, PGPASSWORD: this.password };
    for (let attempt = 0; attempt < 120; attempt += 1) {
      if (this.process?.exitCode !== null) throw new Error("Embedded PostgreSQL exited during startup.");
      try {
        await this.execFile(this.bin("pg_isready"), [
          "-h", "127.0.0.1", "-p", String(this.port), "-U", "wecanfindintern",
        ], { env: environment, timeout: 1000 });
        return;
      } catch (_) {
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
    }
    throw new Error("Embedded PostgreSQL did not become ready within 30 seconds.");
  }

  async ensureApplicationDatabase() {
    const environment = { ...process.env, PGPASSWORD: this.password };
    const { stdout } = await this.execFile(this.bin("psql"), [
      "-h", "127.0.0.1", "-p", String(this.port), "-U", "wecanfindintern",
      "-d", "postgres", "-tAc", "SELECT 1 FROM pg_database WHERE datname='wecanfindintern'",
    ], { env: environment });
    if (stdout.trim() === "1") return;
    await this.execFile(this.bin("createdb"), [
      "-h", "127.0.0.1", "-p", String(this.port),
      "-U", "wecanfindintern", "wecanfindintern",
    ], { env: environment });
  }

  databaseUrl() {
    return `postgresql://wecanfindintern:${encodeURIComponent(this.password)}@127.0.0.1:${this.port}/wecanfindintern`;
  }

  async stop() {
    if (!this.process || this.process.exitCode !== null) {
      this.closeLog();
      return;
    }
    try {
      await this.execFile(this.bin("pg_ctl"), [
        "stop", "-D", this.paths.postgresData, "-m", "fast", "-w", "-t", "20",
      ], { env: { ...process.env, PGPASSWORD: this.password }, timeout: 25000 });
    } catch (error) {
      console.error("Graceful PostgreSQL shutdown failed", error);
      this.process.kill();
    }
    this.closeLog();
  }

  closeLog() {
    if (this.logFd === null) return;
    fs.closeSync(this.logFd);
    this.logFd = null;
  }
}

module.exports = { EmbeddedPostgres };
