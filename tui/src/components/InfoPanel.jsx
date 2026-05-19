import React from "react";
import { Box, Text } from "ink";
import { VERSION } from "../config.js";

const SKILLS = [
  ["memory", "remember  add-person  recall  update-facts"],
  ["wiki", "update-wiki  create-note  link-notes"],
  ["routing", "triage  store-event  extract-facts  file-raw"],
  ["sessions", "debrief  tag-session"],
];

export function InfoPanel({ health }) {
  const backend = health?.backend ?? "—";
  const model = health?.model ?? "—";
  const status = health?.backend_status ?? "—";
  const vault = (health?.vault_last_sync ?? "").slice(0, 10) || "—";
  const tokens = Object.values(health?.token_ledger ?? {}).reduce((a, b) => a + b, 0);

  return (
    <Box borderStyle="single" borderColor="#1e1e1e" marginX={1}>
      <Box flexDirection="column" width={28} paddingX={2} paddingY={1} borderStyle="single" borderTop={false} borderBottom={false} borderLeft={false} borderColor="#1e1e1e">
        <InfoRow label="backend" value={`${backend}/${model}`} />
        <InfoRow label="status" value={status} valueColor={status === "ok" ? "#22c55e" : "#ef4444"} />
        <InfoRow label="vault" value={vault} />
        <InfoRow label="tokens" value={tokens.toLocaleString()} />
        <InfoRow label="build" value={VERSION} />
      </Box>
      <Box flexDirection="column" paddingX={2} paddingY={1} flexGrow={1}>
        <Text color="#555" bold>
          Available Skills
        </Text>
        <Text> </Text>
        {SKILLS.map(([cat, cmds]) => (
          <Box key={cat}>
            <Text color="#e06c00">{cat.padEnd(12)}</Text>
            <Text color="#3a3a3a">{cmds}</Text>
          </Box>
        ))}
      </Box>
    </Box>
  );
}

function InfoRow({ label, value, valueColor = "#555" }) {
  return (
    <Box>
      <Text color="#383838">{`[${label}]`.padEnd(12)}</Text>
      <Text color={valueColor}>{value}</Text>
    </Box>
  );
}
