import React, { useState, useMemo } from "react";
import { Box, Text, useInput } from "ink";
import { getSessionStats, deleteSession, renameSession, listAllSessions } from "../db.js";

function fmtTokens(n) {
  if (n >= 10000) return `${(n / 1000).toFixed(0)}k`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function fmtDate(ts) {
  const d = new Date(ts);
  const mo = d.toLocaleString("en-US", { month: "short", timeZone: "America/Los_Angeles" });
  const day = d.getDate();
  const hh = String(d.toLocaleString("en-US", { hour: "2-digit", hour12: false, timeZone: "America/Los_Angeles" })).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${mo} ${day}  ${hh}:${mm}`;
}

function truncate(str, n) {
  if (!str || str === "new session") return str || "untitled";
  return str.length > n ? str.slice(0, n - 1) + "…" : str;
}

export function SessionManager({ initialSessions, onOpen, onNew, onBack, activeSessionId }) {
  const [sessions, setSessions] = useState(() =>
    initialSessions.map((s) => ({ ...s, stats: getSessionStats(s.id) }))
  );
  const [cursor, setCursor] = useState(0);
  const [mode, setMode] = useState("browse"); // browse | rename | delete-confirm
  const [renameValue, setRenameValue] = useState("");

  const selected = sessions[cursor];

  useInput((input, key) => {
    if (mode === "browse") {
      if (key.downArrow || input === "j") {
        setCursor((c) => Math.min(c + 1, sessions.length - 1));
      } else if (key.upArrow || input === "k") {
        setCursor((c) => Math.max(c - 1, 0));
      } else if (key.return && selected) {
        onOpen(selected.id);
      } else if (input === "n") {
        onNew();
      } else if (input === "r" && selected) {
        setRenameValue(selected.title || "");
        setMode("rename");
      } else if (input === "d" && selected) {
        setMode("delete-confirm");
      } else if ((input === "q" || key.escape) && onBack) {
        onBack();
      }
      return;
    }

    if (mode === "rename") {
      if (key.return) {
        const trimmed = renameValue.trim();
        if (trimmed) {
          renameSession(selected.id, trimmed);
          setSessions((prev) =>
            prev.map((s) => (s.id === selected.id ? { ...s, title: trimmed } : s))
          );
        }
        setMode("browse");
      } else if (key.escape) {
        setMode("browse");
      } else if (key.backspace || key.delete) {
        setRenameValue((v) => v.slice(0, -1));
      } else if (!key.ctrl && !key.meta && input) {
        setRenameValue((v) => v + input);
      }
      return;
    }

    if (mode === "delete-confirm") {
      if (input === "y" || input === "Y") {
        const id = selected.id;
        deleteSession(id);
        const next = sessions.filter((s) => s.id !== id);
        setSessions(next);
        setCursor((c) => Math.min(c, Math.max(next.length - 1, 0)));
        setMode("browse");
      } else if (input === "n" || input === "N" || key.escape) {
        setMode("browse");
      }
    }
  });

  return (
    <Box flexDirection="column" paddingX={3} paddingTop={1}>
      {/* Header */}
      <Box marginBottom={1}>
        <Text color="#555" bold>SESSIONS  </Text>
        <Text color="#2a2a2a">{sessions.length}</Text>
        {activeSessionId && (
          <Text color="#1e1e1e">  {" · q to go back"}</Text>
        )}
      </Box>

      {/* Column headers */}
      <Box>
        <Text color="#1e1e1e">{"   "}</Text>
        <Text color="#1e1e1e">{"title".padEnd(42)}</Text>
        <Text color="#1e1e1e">{"msgs".padStart(6)}</Text>
        <Text color="#1e1e1e">{"tokens".padStart(8)}</Text>
        <Text color="#1e1e1e">{"chars".padStart(8)}</Text>
        <Text color="#1e1e1e">{"  last active"}</Text>
      </Box>
      <Text color="#1e1e1e">{"─".repeat(80)}</Text>

      {/* Session rows */}
      {sessions.length === 0 && (
        <Text color="#333">  no sessions yet</Text>
      )}

      {sessions.map((s, i) => {
        const isSelected = i === cursor;
        const isActive = s.id === activeSessionId;
        const isDeleting = isSelected && mode === "delete-confirm";
        const isRenaming = isSelected && mode === "rename";

        const titleColor = isDeleting ? "#7a2020" : isSelected ? "#e0e0e0" : "#383838";
        const dimColor = isDeleting ? "#5a1010" : isSelected ? "#555" : "#222";
        const prefix = isSelected ? "↳ " : isActive ? "● " : "  ";
        const prefixColor = isSelected ? "#e06c00" : isActive ? "#555" : "#1a1a1a";

        let titleDisplay;
        if (isRenaming) {
          titleDisplay = (
            <Box>
              <Text color="#d0d0d0">{renameValue}</Text>
              <Text color="#e06c00">{"▌"}</Text>
              <Text color="#1e1e1e">{" ".repeat(Math.max(0, 40 - renameValue.length - 2))}</Text>
            </Box>
          );
        } else {
          titleDisplay = (
            <Text color={titleColor}>{truncate(s.title, 40).padEnd(40)}</Text>
          );
        }

        return (
          <Box key={s.id}>
            <Text color={prefixColor}>{prefix}</Text>
            {titleDisplay}
            <Text color={dimColor}>{String(s.stats.messageCount).padStart(6)}</Text>
            <Text color={dimColor}>{fmtTokens(s.stats.estimatedTokens).padStart(8)}</Text>
            <Text color={isSelected ? "#333" : "#1e1e1e"}>{String(s.stats.totalChars).padStart(8)}</Text>
            <Text color={isSelected ? "#333" : "#1a1a1a"}>{"  " + fmtDate(s.updated_at)}</Text>
          </Box>
        );
      })}

      <Text color="#1e1e1e">{"─".repeat(80)}</Text>

      {/* Help bar */}
      <Box marginTop={1}>
        {mode === "browse" && (
          <Text color="#2a2a2a">
            {"  j/k navigate   r rename   d delete   n new session   Enter open"}
            {onBack ? "   q back" : ""}
          </Text>
        )}
        {mode === "rename" && (
          <Text color="#555">{"  rename — Enter confirm   Escape cancel"}</Text>
        )}
        {mode === "delete-confirm" && (
          <Text color="#7a2020">{"  delete \"" + truncate(selected?.title, 30) + "\"?  y / n"}</Text>
        )}
      </Box>
    </Box>
  );
}
