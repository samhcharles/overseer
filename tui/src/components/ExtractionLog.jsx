import React from "react";
import { Box, Text } from "ink";

export function ExtractionLog({ extractions }) {
  if (!extractions.length) {
    return (
      <Box borderStyle="single" borderColor="#1e1e1e" marginX={1} paddingX={2} paddingY={1}>
        <Text color="#333">  [extraction log — nothing extracted this session yet]</Text>
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
    <Box flexDirection="column" borderStyle="single" borderColor="#1e1e1e" marginX={1} paddingX={2} paddingY={1}>
      <Text color="#333">  [extraction log — this session]</Text>
      {rows.slice(-12).map((r, i) => (
        <Box key={i}>
          <Text color="#2a2a2a">{`  ${r.time}  `}</Text>
          <Text color="#383838">{r.label}</Text>
        </Box>
      ))}
      <Text color="#2a2a2a">  /extracted to hide</Text>
    </Box>
  );
}
