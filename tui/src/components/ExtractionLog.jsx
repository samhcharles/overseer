import React from "react";
import { Box, Text } from "ink";

function truncate(value, maxLength = 42) {
  if (!value) return "";
  if (value.length <= maxLength) return value;
  return `${value.slice(0, Math.max(0, maxLength - 1))}…`;
}

export function ExtractionLog({ extractions }) {
  if (!extractions.length) {
    return (
      <Box borderStyle="round" borderColor="#444" paddingX={1} paddingY={0} flexDirection="column">
        <Text color="#888" bold>EXTRACTIONS</Text>
        <Text color="#666">Nothing extracted in this session yet.</Text>
      </Box>
    );
  }

  const rows = [];
  for (const ex of extractions) {
    const time = new Date(ex.created_at).toLocaleTimeString("en-US", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone: "America/Los_Angeles",
    });
    const writes = JSON.parse(ex.vault_writes || "[]");
    const entities = JSON.parse(ex.entities || "{}");

    const labels = [];
    for (const p of entities.people || []) labels.push(`${p.name} → wiki/personal/people/${p.name.toLowerCase().replace(/ /g, "-")}.md`);
    for (const t of entities.todos || []) labels.push(`todo: ${t.task}`);
    for (const e of entities.events || []) labels.push(`event: ${e.description}`);
    for (const w of writes) {
      if (!labels.some((l) => l.includes(w))) labels.push(w);
    }

    for (const label of labels) {
      rows.push({ time, label });
    }
  }

  return (
    <Box flexDirection="column" borderStyle="round" borderColor="#444" paddingX={1} paddingY={0}>
      <Text color="#888" bold>EXTRACTIONS</Text>
      <Text color="#666">{`${rows.length} captured event${rows.length === 1 ? "" : "s"} this session`}</Text>
      {rows.slice(-12).map((r, i) => (
        <Box key={i}>
          <Text color="#777">{`${r.time}  `}</Text>
          <Text color="#d0d0d0">{truncate(r.label)}</Text>
        </Box>
      ))}
      <Text color="#666">/extracted to hide</Text>
    </Box>
  );
}
