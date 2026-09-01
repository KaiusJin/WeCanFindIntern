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
  constructor(paths, password) {
    this.paths = paths;
    this.password = password;
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

  async start() {
    await this.verifyBundle();
    await this.initialize();
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
        await execFileAsync(this.bin("pg_isready"), [
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
    const { stdout } = await execFileAsync(this.bin("psql"), [
      "-h", "127.0.0.1", "-p", String(this.port), "-U", "wecanfindintern",
      "-d", "postgres", "-tAc", "SELECT 1 FROM pg_database WHERE datname='wecanfindintern'",
    ], { env: environment });
    if (stdout.trim() === "1") return;
    await execFileAsync(this.bin("createdb"), [
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
      await execFileAsync(this.bin("pg_ctl"), [
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
