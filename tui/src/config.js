import { readFileSync } from "fs";
import { homedir } from "os";
import { join } from "path";

function loadEnv() {
  const envPath = join(homedir(), ".secrets", "master.env");
  try {
    const lines = readFileSync(envPath, "utf8").split("\n");
    for (const line of lines) {
      if (!line.includes("=") || line.startsWith("#")) continue;
      const [k, ...rest] = line.split("=");
      if (k.trim() === "OVERSEER_API_URL") {
        return rest.join("=").trim().replace(/^['"]|['"]$/g, "").replace(/\/$/, "");
      }
    }
  } catch {}
  return "http://100.73.12.59:8765";
}

export const API_URL = loadEnv();
export const VERSION = "0.1.0";
export const DB_PATH = join(homedir(), ".overseer", "history.db");
