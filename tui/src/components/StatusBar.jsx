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

export function StatusBar({ health, elapsed, lastBackend }) {
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
  const barColor = isFallback ? "#e06c00" : "#666";
  const apiHost = shortEndpoint(health?.api_url ?? health?.api_urls?.[0]);

  return (
    <Box paddingX={2} paddingBottom={0} justifyContent="space-between">
      <Text color={barColor}>{`  ${backendDisplay} · ${tokens.toLocaleString()} ctx · ${apiHost}`}</Text>
      <Text color="#666">{`${elapsedStr}  ${now} PST  `}</Text>
    </Box>
  );
}
