const crypto = require("node:crypto");
const fs = require("node:fs/promises");
const path = require("node:path");
const { safeStorage } = require("electron");

async function readSecrets(filePath) {
  try {
    return JSON.parse(await fs.readFile(filePath, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") return {};
    throw error;
  }
}

async function writeSecrets(filePath, payload) {
  if (!safeStorage.isEncryptionAvailable()) {
    throw new Error("Operating-system secure storage is unavailable.");
  }
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  const temporaryPath = `${filePath}.tmp`;
  await fs.writeFile(temporaryPath, JSON.stringify(payload, null, 2), { mode: 0o600 });
  await fs.rename(temporaryPath, filePath);
  await fs.chmod(filePath, 0o600);
}

async function getOrCreateSecret(userData, name) {
  const filePath = path.join(userData, "secrets.json");
  const payload = await readSecrets(filePath);
  if (payload[name]) {
    return safeStorage.decryptString(Buffer.from(payload[name], "base64"));
  }
  if (!safeStorage.isEncryptionAvailable()) {
    throw new Error("Operating-system secure storage is unavailable.");
  }
  const value = crypto.randomBytes(36).toString("base64url");
  payload[name] = safeStorage.encryptString(value).toString("base64");
  await writeSecrets(filePath, payload);
  return value;
}

async function getSecrets(userData, names) {
  const payload = await readSecrets(path.join(userData, "secrets.json"));
  return Object.fromEntries(names.map((name) => [
    name,
    payload[name] ? safeStorage.decryptString(Buffer.from(payload[name], "base64")) : "",
  ]));
}

async function setSecrets(userData, values) {
  const filePath = path.join(userData, "secrets.json");
  const payload = await readSecrets(filePath);
  for (const [name, value] of Object.entries(values)) {
    if (value) payload[name] = safeStorage.encryptString(String(value)).toString("base64");
    else delete payload[name];
  }
  await writeSecrets(filePath, payload);
}

module.exports = { getOrCreateSecret, getSecrets, setSecrets };
