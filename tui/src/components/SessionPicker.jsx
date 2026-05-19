import React from "react";
import { Box, Text, useInput } from "ink";

function formatDate(ts) {
  return new Date(ts).toLocaleString("en-US", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "America/Los_Angeles",
  });
}

function truncate(str, n) {
  return str && str.length > n ? str.slice(0, n - 1) + "…" : str || "—";
}

export function SessionPicker({ sessions, onSelect }) {
  useInput((input, key) => {
    if (input === "n") {
      onSelect(null);
      return;
    }
    const n = parseInt(input);
    if (!isNaN(n) && n >= 1 && n <= sessions.length) {
      onSelect(sessions[n - 1].id);
    }
  });

  return (
    <Box flexDirection="column" paddingX={4} paddingTop={2}>
      <Text color="#555" bold>
        Recent sessions
      </Text>
      <Text color="#1e1e1e">{"─".repeat(48)}</Text>
      {sessions.map((s, i) => (
        <Box key={s.id}>
          <Text color="#383838">{`  ${i + 1}  `}</Text>
          <Text color="#555">{formatDate(s.updated_at).padEnd(18)}</Text>
          <Text color="#3a3a3a">{truncate(s.title, 36)}</Text>
        </Box>
      ))}
      <Text color="#1e1e1e">{"─".repeat(48)}</Text>
      <Text color="#2a2a2a">{"  n  new session"}</Text>
      <Text> </Text>
      <Text color="#1e1e1e">Press 1–{sessions.length} or n</Text>
    </Box>
  );
}
