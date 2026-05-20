import { readFileSync } from "fs";
import { homedir } from "os";
import { join } from "path";

const DEFAULT_API_URLS = ["http://127.0.0.1:8765"];

function normalizeUrl(value) {
  return value.trim().replace(/\/$/, "");
}

function parseEnvContent(content) {
  const env = {};
  for (const line of content.split("\n")) {
    if (!line.includes("=") || line.startsWith("#")) continue;
    const [k, ...rest] = line.split("=");
    env[k.trim()] = rest.join("=").trim().replace(/^['"]|['"]$/g, "");
  }
  return env;
}

function parseApiUrls(primary, candidates) {
  const values = [];
  if (primary?.trim()) values.push(primary);
  for (const value of (candidates || "").split(",")) {
    if (value.trim()) values.push(value);
  }
  return [...new Set(values.map(normalizeUrl).filter(Boolean))];
}

function withLocalFallback(apiUrls) {
  return [...new Set([...apiUrls, ...DEFAULT_API_URLS])];
}

function loadEnv() {
  const envPath = join(homedir(), ".secrets", "master.env");
  let fileEnv = {};
  try {
    fileEnv = parseEnvContent(readFileSync(envPath, "utf8"));
  } catch {}

  const processApiUrls = parseApiUrls(
    process.env.OVERSEER_API_URL || "",
    process.env.OVERSEER_API_CANDIDATES || "",
  );
  const fileApiUrls = parseApiUrls(
    fileEnv.OVERSEER_API_URL || "",
    fileEnv.OVERSEER_API_CANDIDATES || "",
  );
  const apiUrls = processApiUrls.length
    ? withLocalFallback(processApiUrls)
    : fileApiUrls.length
      ? withLocalFallback(fileApiUrls)
      : DEFAULT_API_URLS;

  const apiKey = (process.env.OVERSEER_API_KEY || fileEnv.OVERSEER_API_KEY || "").trim();
  const apiSource = processApiUrls.length
    ? "process.env"
    : fileApiUrls.length
      ? envPath
      : "default(localhost)";

  return { apiUrls, apiKey, apiSource };
}

const { apiUrls, apiKey, apiSource } = loadEnv();
export const API_URLS = apiUrls;
export const API_URL = apiUrls[0];
export const API_KEY = apiKey;
export const API_SOURCE = apiSource;
export const VERSION = "0.1.0";
export const DB_PATH = join(homedir(), ".overseer", "history.db");
