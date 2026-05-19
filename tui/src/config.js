import { readFileSync } from "fs";
import { homedir } from "os";
import { join } from "path";

function loadEnv() {
  const envPath = join(homedir(), ".secrets", "master.env");
  let apiUrl = "http://100.73.12.59:8765";
  let apiKey = "";
  try {
    const lines = readFileSync(envPath, "utf8").split("\n");
    for (const line of lines) {
      if (!line.includes("=") || line.startsWith("#")) continue;
      const [k, ...rest] = line.split("=");
      const val = rest.join("=").trim().replace(/^['"]|['"]$/g, "");
      if (k.trim() === "OVERSEER_API_URL") apiUrl = val.replace(/\/$/, "");
      if (k.trim() === "OVERSEER_API_KEY") apiKey = val;
    }
  } catch {}
  return { apiUrl, apiKey };
}

const { apiUrl, apiKey } = loadEnv();
export const API_URL = apiUrl;
export const API_KEY = apiKey;
export const VERSION = "0.1.0";
export const DB_PATH = join(homedir(), ".overseer", "history.db");
