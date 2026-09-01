const fs = require("node:fs");
const path = require("node:path");

const projectRoot = path.resolve(__dirname, "..");
const nativeTarget = `${process.platform}-${process.arch}`;
const extraResource = [
  path.join(projectRoot, "web"),
  path.join(projectRoot, "migrations"),
  path.join(projectRoot, "config"),
  path.join(__dirname, "resources", "backend"),
  path.join(__dirname, "resources", "postgres"),
]
  .filter((source) => fs.existsSync(source));

module.exports = {
  packagerConfig: {
    asar: true,
    executableName: "WeCanFindIntern",
    appBundleId: "com.wecanfindintern.desktop",
    icon: path.join(__dirname, "assets", process.platform === "darwin" ? "icon.icns" : "icon.ico"),
    extraResource,
    ignore: [/^\/assets(?:\/|$)/, /^\/resources(?:\/|$)/, /^\/out(?:\/|$)/],
    osxSign: process.env.APPLE_IDENTITY
      ? { identity: process.env.APPLE_IDENTITY, hardenedRuntime: true }
      : undefined,
    osxNotarize:
      process.env.APPLE_ID && process.env.APPLE_APP_SPECIFIC_PASSWORD && process.env.APPLE_TEAM_ID
        ? {
            appleId: process.env.APPLE_ID,
            appleIdPassword: process.env.APPLE_APP_SPECIFIC_PASSWORD,
            teamId: process.env.APPLE_TEAM_ID,
          }
        : undefined,
  },
  makers: [
    { name: "@electron-forge/maker-zip", platforms: ["darwin"] },
    { name: "@electron-forge/maker-dmg", config: { format: "ULFO" } },
    {
      name: "@electron-forge/maker-squirrel",
      config: {
        name: "WeCanFindIntern",
        setupExe: "WeCanFindInternSetup.exe",
        setupIcon: path.join(__dirname, "assets", "icon.ico"),
      },
    },
  ],
  hooks: {
    prePackage: async () => {
      const executableSuffix = process.platform === "win32" ? ".exe" : "";
      const required = [
        path.join(projectRoot, "web", "index.html"),
        path.join(projectRoot, "migrations", "0001_job_data.sql"),
        path.join(projectRoot, "config", "collection_plans.json"),
        path.join(
          __dirname,
          "resources", "backend", nativeTarget, "wecanfindintern-backend",
          `wecanfindintern-backend${executableSuffix}`,
        ),
        path.join(__dirname, "resources", "postgres", nativeTarget, "bin", `postgres${executableSuffix}`),
        path.join(__dirname, "resources", "postgres", nativeTarget, "bin", `pg_restore${executableSuffix}`),
      ];
      const missing = required.filter((candidate) => !fs.existsSync(candidate));
      if (missing.length) {
        throw new Error(`Desktop native resources are incomplete:\n${missing.join("\n")}`);
      }
    },
  },
};
