import React from "react";
import { Box, Text } from "ink";

function shortEndpoint(url) {
  if (!url) return "-";
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function shortModel(model) {
  if (!model) return null;
  const normalized = String(model).trim();
  if (!normalized) return null;
  if (!normalized.includes("/")) return normalized;
  const parts = normalized.split("/");
  return parts[parts.length - 1];
}

export function StatusBar({ health, elapsed, lastBackend, lastModel, currentMode = "chat" }) {
  const configured = health?.backend ?? "-";
  const tokens = Object.values(health?.token_ledger ?? {}).reduce((a, b) => a + b, 0);
  const elapsedStr = elapsed != null ? `⚡ ${elapsed.toFixed(1)}s` : "";
  const now = new Date().toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "America/Los_Angeles",
    hour12: false,
  });

  const isFallback = lastBackend && lastBackend !== configured;
  const backendDisplay = isFallback ? `${configured}->${lastBackend}` : configured;
  const modelDisplay = shortModel(lastModel);
  const barColor = "#666";
  const apiHost = shortEndpoint(health?.api_url ?? health?.api_urls?.[0]);

  return (
    <Box paddingX={1} paddingBottom={0}>
      <Box borderStyle="round" borderColor="#444" paddingX={1} paddingY={0} justifyContent="space-between" flexGrow={1}>
        <Text color={barColor}>{`${currentMode} · ${backendDisplay}${modelDisplay ? ` · ${modelDisplay}` : ""} · ${tokens.toLocaleString()} ctx · ${apiHost}`}</Text>
        <Text color="#666">{`${elapsedStr}${elapsedStr ? "  " : ""}${now} PST`}</Text>
      </Box>
    </Box>
  );
}
