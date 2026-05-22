#!/usr/bin/env node
import { randomUUID } from "crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from "fs";
import { homedir } from "os";
import { dirname, join, relative } from "path";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { Box, Text, render, useApp, useInput, useStdout } from "ink";
import { VERSION } from "./config.js";
import { extractText, fetchHealth, fetchProviders, formatApiError, getApiRuntime, sendChat, updateProviders } from "./api.js";
import { IntroBanner, Sprite } from "./components/Banner.jsx";
import { ThinkingIndicator } from "./components/ThinkingDots.jsx";
import { SessionManager } from "./components/SessionManager.jsx";
import {
  cleanRuntimeValue,
  currentModelLabel,
  displayPath,
  formatRuntimeDescriptor,
  shortEndpoint,
  shortModel,
} from "./runtime.js";
import {
  createSession,
  updateSessionTitle,
  updateSessionMode,
  listAllSessions,
  addMessage,
  getMessages,
  addExtraction,
  getExtractions,
  listRecentMessages,
} from "./db.js";

const COMMANDS = [
  ["/chat", "return to the main chat shell"],
  ["/vault", "browse vault files"],
  ["/sessions", "view saved sessions"],
  ["/history", "view recent message history"],
  ["/usage", "view tokens, nodes, and runtime status"],
  ["/providers", "manage providers, models, and runtime pinning"],
  ["/models", "same as /providers"],
  ["/health", "show gateway status"],
  ["/model", "show active runtime"],
  ["/help", "show commands"],
  ["/quit", "exit Overseer"],
];

const KEYBINDINGS_TEXT = `Keyboard shortcuts:
  Enter          send message
  Shift+Enter    new line  (Ctrl+J if your terminal can't distinguish)
  Shift+Tab      cycle mode (chat → think → capture)
  /              open command picker
  ?              show this help
  Esc            stop running request
  Ctrl+C × 2    exit Overseer
  ↑ / ↓         navigate command picker`;

const VIEW_TITLES = {
  chat: "chat",
  vault: "vault",
  sessions: "sessions",
  history: "history",
  usage: "usage",
  providers: "providers",
};

const MAX_VISIBLE_MESSAGES = 28;
const HISTORY_LIMIT = 120;
const VISIBLE_HISTORY_ROWS = 18;
const WIDE_VAULT_LAYOUT_MIN = 110;

const MODES = [
  {
    id: "chat",
    indicator: "[o]",
    title: "chat with Overseer",
    status: "read-only",
    composerHint: "Read-only chat. Nothing is saved in this mode.",
    accent: "#e06c00",
    dimAccent: "#7d3500",
  },
  {
    id: "think",
    indicator: "[!]",
    title: "think with Overseer",
    status: "private",
    composerHint: "Think, plan, and clarify. Nothing is saved.",
    accent: "#6366f1",
    dimAccent: "#3730a3",
  },
  {
    id: "capture",
    indicator: "[+]",
    title: "capture to vault",
    status: "write-enabled",
    composerHint: "Capture mode. Overseer may extract and write durable notes.",
    accent: "#10b981",
    dimAccent: "#065f46",
  },
];

const MODE_BY_ID = Object.fromEntries(MODES.map((mode) => [mode.id, mode]));

function tokenCount(health) {
  return Object.values(health?.token_ledger ?? {}).reduce((a, b) => a + b, 0);
}

function summarizeToolCalls(toolCalls) {
  if (!toolCalls?.length) return null;
  const tally = {};
  for (const call of toolCalls) {
    const raw = (call?.function?.name ?? call?.name ?? String(call)).toLowerCase();
    let bucket;
    if (/read|file|open|cat|view/.test(raw)) bucket = "read";
    else if (/list|dir|ls|readdir/.test(raw)) bucket = "listed dirs";
    else if (/write|save|append|create/.test(raw)) bucket = "wrote";
    else if (/search|grep|find|glob/.test(raw)) bucket = "searched";
    else bucket = raw.slice(0, 16);
    tally[bucket] = (tally[bucket] || 0) + 1;
  }
  const parts = [];
  if (tally.read) parts.push(`read ${tally.read} file${tally.read > 1 ? "s" : ""}`);
  if (tally["listed dirs"]) parts.push(`listed ${tally["listed dirs"]} dir${tally["listed dirs"] > 1 ? "s" : ""}`);
  if (tally.wrote) parts.push(`wrote ${tally.wrote} file${tally.wrote > 1 ? "s" : ""}`);
  if (tally.searched) parts.push(`searched ${tally.searched}×`);
  for (const [k, v] of Object.entries(tally)) {
    if (!["read", "listed dirs", "wrote", "searched"].includes(k)) parts.push(`${k} ×${v}`);
  }
  return parts.join(" · ");
}

function countVaultFiles() {
  try {
    const vaultRoot = join(homedir(), "vault");
    if (!existsSync(vaultRoot)) return null;
    let count = 0;
    const walk = (dir, depth) => {
      if (depth > 6 || count > 9999) return;
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        if (entry.name.startsWith(".")) continue;
        if (entry.isDirectory()) walk(join(dir, entry.name), depth + 1);
        else if (entry.name.endsWith(".md")) count++;
      }
    };
    walk(vaultRoot, 0);
    return count;
  } catch {
    return null;
  }
}

function writeVaultConversationNote(sessionId, userText, assistantText) {
  try {
    const vaultRoot = join(homedir(), "vault");
    if (!existsSync(vaultRoot)) return;
    const convDir = join(vaultRoot, "conversations");
    mkdirSync(convDir, { recursive: true });
    const noteFile = join(convDir, `overseer-${sessionId.slice(0, 8)}.md`);
    const timestamp = new Date().toISOString().slice(0, 19).replace("T", " ");
    let content = existsSync(noteFile)
      ? readFileSync(noteFile, "utf8")
      : `---\ntags: [overseer, conversation]\ncreated: ${timestamp}\n---\n\n# Overseer Session\n\n`;
    content += `## ${timestamp}\n\n**You:** ${userText}\n\n**Overseer:** ${assistantText}\n\n---\n\n`;
    writeFileSync(noteFile, content, "utf8");
  } catch {
    // Non-fatal — vault write failure must not crash the UI
  }
}

function truncateText(value, maxLength = 80) {
  if (!value) return "";
  if (value.length <= maxLength) return value;
  return `${value.slice(0, Math.max(0, maxLength - 1))}…`;
}

function modeConfig(modeId) {
  return MODE_BY_ID[modeId] ?? MODE_BY_ID.chat;
}

function starterPrompts(modeId) {
  if (modeId === "think") {
    return [
      "Help me think through this before we build anything.",
      "Compare these two directions and show me the tradeoffs.",
      "Ask me the questions you need to really understand the goal.",
    ];
  }
  if (modeId === "capture") {
    return [
      "Capture this note into the right place in the vault.",
      "Extract the durable facts from what I just said.",
      "Turn this rough thought into a clean saved note.",
    ];
  }
  return [
    "What does my vault already know about this topic?",
    "What model and runtime are you using right now?",
    "Summarize the current context before we act.",
  ];
}

function formatTime(timestamp) {
  if (!timestamp) return "";
  return new Date(timestamp).toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function formatDate(timestamp) {
  if (!timestamp) return "-";
  const date = new Date(timestamp);
  const month = date.toLocaleString("en-US", { month: "short" });
  const day = date.getDate();
  return `${month} ${day} ${formatTime(timestamp)}`;
}

function formatHealthSummary(health) {
  const parts = [
    `status=${health.backend_status ?? "-"}`,
    `backend=${health.backend ?? "-"}`,
    `url=${health.api_url ?? health.api_urls?.[0] ?? getApiRuntime().defaultUrl}`,
    `source=${health.api_source ?? getApiRuntime().source}`,
  ];

  if (health.connection_error) {
    parts.push(`error=${health.connection_error}`);
  } else {
    parts.push(`vault=${(health.vault_last_sync || "").slice(0, 10) || "-"}`);
    parts.push(`nodes=${health.trusted_node_count ?? 0}`);
    parts.push(`tokens=${tokenCount(health)}`);
  }

  return parts.join("  ");
}

function makeMessage({
  role,
  content,
  toolCalls = [],
  timestamp = Date.now(),
  backendUsed = null,
  modelUsed = null,
  tone = "default",
}) {
  return {
    id: randomUUID(),
    role,
    content,
    toolCalls,
    timestamp,
    backendUsed,
    modelUsed,
    tone,
  };
}

function runtimeLabel(health, lastBackend) {
  return lastBackend ?? health?.backend ?? "-";
}

function slotSummary(health) {
  const active = health?.active_slots ?? [];
  const blocked = Object.keys(health?.blocked_slots ?? {});
  if (!active.length && !blocked.length) return "no provider slots";
  return `${active.length} active · ${blocked.length} blocked`;
}

function providerStatusLabel(provider) {
  if (!provider) return "unknown";
  if (!provider.configured) return "not configured";
  return provider.status || "idle";
}

function providerEnabledLabel(provider) {
  return provider?.enabled ? "enabled" : "muted";
}

function buildProviderRows(providerState) {
  if (!providerState) return [];
  const providers = providerState.providers || {};
  const openrouterModels = providers.openrouter?.models || [];
  const localModels = providers.ollama?.available_models || [];

  return [
    {
      key: "backend",
      label: "backend pin",
      value: providerState.backend || "auto",
      help: `Edit to auto, node, or one of: ${(providerState.available_backends || []).join(", ") || "auto"}`,
      editField: "backend",
      defaultAction: "edit",
    },
    {
      key: "prefer_local_nodes",
      label: "prefer local nodes",
      value: providerState.prefer_local_nodes ? "on" : "off",
      help: "Toggle whether trusted Tailscale nodes should run before cloud rotation.",
      togglePatch: { prefer_local_nodes: !providerState.prefer_local_nodes },
      defaultAction: "toggle",
    },
    {
      key: "gemini-enabled",
      label: "gemini",
      value: `${providerEnabledLabel(providers.gemini)} · ${providerStatusLabel(providers.gemini)}`,
      help: providers.gemini?.configured ? "Toggle Gemini in the runtime rotator." : "Gemini key is not configured on this gateway.",
      togglePatch: { gemini_enabled: !providers.gemini?.enabled },
      defaultAction: "toggle",
    },
    {
      key: "gemini-model",
      label: "gemini model",
      value: providers.gemini?.model || "-",
      help: "Edit the Gemini model name used when Gemini is selected.",
      editField: "gemini_model",
      defaultAction: "edit",
    },
    {
      key: "groq-enabled",
      label: "groq",
      value: `${providerEnabledLabel(providers.groq)} · ${providerStatusLabel(providers.groq)}`,
      help: providers.groq?.configured ? "Toggle Groq in the runtime rotator." : "Groq key is not configured on this gateway.",
      togglePatch: { groq_enabled: !providers.groq?.enabled },
      defaultAction: "toggle",
    },
    {
      key: "groq-model",
      label: "groq model",
      value: providers.groq?.model || "-",
      help: "Edit the Groq model name used when Groq is selected.",
      editField: "groq_model",
      defaultAction: "edit",
    },
    {
      key: "openrouter-enabled",
      label: "openrouter",
      value: `${providerEnabledLabel(providers.openrouter)} · ${providerStatusLabel(providers.openrouter)}`,
      help: providers.openrouter?.configured ? "Toggle OpenRouter slots in the runtime rotator." : "OpenRouter key is not configured on this gateway.",
      togglePatch: { openrouter_enabled: !providers.openrouter?.enabled },
      defaultAction: "toggle",
    },
    {
      key: "openrouter-primary",
      label: "openrouter primary",
      value: providers.openrouter?.model || "-",
      help: "Edit the pinned OpenRouter model used when backend pinning targets OpenRouter.",
      editField: "openrouter_model",
      defaultAction: "edit",
    },
    {
      key: "openrouter-add",
      label: "openrouter slots",
      value: `${openrouterModels.length} configured`,
      help: "Press a to add a new :free OpenRouter model.",
      editor: {
        field: "openrouter_models_add",
        label: "add openrouter slot",
        initialValue: "",
        submitLabel: "Add a new :free model. It will be appended to the rotation list.",
      },
    },
    ...openrouterModels.map((modelName, index) => ({
      key: `openrouter-slot-${index}`,
      label: `slot ${index + 1}`,
      value: modelName,
      help: "p pins this model for OpenRouter, d removes it from rotation.",
      pinPatch: { openrouter_model: modelName },
      removeOpenrouterModel: modelName,
      defaultAction: "pin",
    })),
    {
      key: "ollama-enabled",
      label: "ollama",
      value: `${providerEnabledLabel(providers.ollama)} · ${providerStatusLabel(providers.ollama)}`,
      help: providers.ollama?.url ? "Toggle the local Ollama gateway in the runtime rotator." : "No Ollama URL is configured yet.",
      togglePatch: { ollama_enabled: !providers.ollama?.enabled },
      defaultAction: "toggle",
    },
    {
      key: "ollama-url",
      label: "ollama url",
      value: providers.ollama?.url || "-",
      help: "Edit the local Ollama base URL.",
      editField: "ollama_url",
      defaultAction: "edit",
    },
    {
      key: "ollama-model",
      label: "ollama model",
      value: providers.ollama?.model || "-",
      help: "Edit the active local Ollama model.",
      editField: "ollama_model",
      defaultAction: "edit",
    },
    ...localModels.map((modelName, index) => ({
      key: `local-model-${index}`,
      label: `local ${index + 1}`,
      value: modelName,
      help: "Enter or u sets this discovered local model as the active Ollama model.",
      applyPatch: { ollama_model: modelName },
      defaultAction: "apply",
    })),
  ];
}

function vaultRoot() {
  const candidates = [
    process.env.VAULT_PATH,
    join(homedir(), "vault"),
    join(homedir(), "vault", "wiki"),
  ].filter(Boolean);
  return candidates.find((candidate) => existsSync(candidate)) ?? candidates[0];
}

function safeVaultPath(root, targetPath) {
  const next = targetPath || root;
  const rel = relative(root, next);
  if (rel.startsWith("..")) return root;
  return next;
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatVaultLocation(root, currentPath) {
  const rel = relative(root, currentPath);
  return rel ? `/${rel}` : "/";
}

function loadVaultDirectory(targetPath) {
  const root = vaultRoot();
  if (!root || !existsSync(root)) {
    return {
      root: root || join(homedir(), "vault"),
      currentPath: root || join(homedir(), "vault"),
      entries: [],
      cursor: 0,
      error: `vault not found at ${root || join(homedir(), "vault")}`,
    };
  }

  const currentPath = safeVaultPath(root, targetPath || root);
  let dirPath = currentPath;
  try {
    if (!statSync(dirPath).isDirectory()) {
      dirPath = dirname(dirPath);
    }
  } catch {
    dirPath = root;
  }

  try {
    const entries = readdirSync(dirPath, { withFileTypes: true })
      .filter((entry) => !entry.name.startsWith("."))
      .map((entry) => {
        const fullPath = join(dirPath, entry.name);
        const stat = statSync(fullPath);
        return {
          name: entry.name,
          fullPath,
          type: entry.isDirectory() ? "directory" : "file",
          size: stat.size,
          mtimeMs: stat.mtimeMs,
        };
      })
      .sort((left, right) => {
        if (left.type !== right.type) return left.type === "directory" ? -1 : 1;
        return left.name.localeCompare(right.name);
      });

    return {
      root,
      currentPath: dirPath,
      entries,
      cursor: 0,
      error: null,
    };
  } catch (error) {
    return {
      root,
      currentPath: dirPath,
      entries: [],
      cursor: 0,
      error: error?.message || "unable to read vault directory",
    };
  }
}

function readVaultPreview(entry) {
  if (!entry) return "No file selected.";
  if (entry.type === "directory") {
    try {
      const children = readdirSync(entry.fullPath).filter((name) => !name.startsWith("."));
      const header = `${entry.name}/\n${children.length} visible entries`;
      const sample = children.slice(0, 12).map((name) => `- ${name}`).join("\n");
      return sample ? `${header}\n\n${sample}` : header;
    } catch (error) {
      return error?.message || "unable to read directory preview";
    }
  }

  try {
    const content = readFileSync(entry.fullPath, "utf8").replace(/\t/g, "  ");
    return content.split("\n").slice(0, 40).join("\n") || "(empty file)";
  } catch (error) {
    return error?.message || "unable to preview file";
  }
}

function Divider({ columns, color = "#333" }) {
  return <Text color={color}>{"─".repeat(Math.max(24, columns - 2))}</Text>;
}

function ShellHeader({ currentMode, currentView, health, lastBackend, lastModel, cwd = process.cwd() }) {
  const mode = modeConfig(currentMode);
  const runtime = cleanRuntimeValue(runtimeLabel(health, lastBackend)) ?? "rotator";
  const model = currentModelLabel(lastModel, health);
  const viewTitle = VIEW_TITLES[currentView] ?? currentView;
  const runtimeDescriptor = formatRuntimeDescriptor(runtime, model);
  const statusLine = currentView === "chat"
    ? `${mode.title} · ${runtimeDescriptor}`
    : `${viewTitle} view · ${runtimeDescriptor}`;
  const accent = health?.backend_status === "ok" ? mode.accent : "#d65c4a";
  const dimAcc = health?.backend_status === "ok" ? mode.dimAccent : "#7d3a08";

  return (
    <Box paddingX={1} paddingTop={1}>
      <Sprite accent={accent} dimAccent={dimAcc} />
      <Box marginLeft={1} flexDirection="column">
        <Text color="#d8d8d8" bold>{`Overseer v${VERSION}`}</Text>
        <Text color={accent}>{statusLine}</Text>
        <Text color="#8a8a8a">{displayPath(cwd)}</Text>
      </Box>
    </Box>
  );
}

function FooterBar({ currentView, currentMode, sending, showCommandPicker, vaultFileCount, activeModel }) {
  const mode = modeConfig(currentMode);

  if (currentView === "chat") {
    const fileCountText = vaultFileCount != null ? `${vaultFileCount.toLocaleString()} vault files` : "";
    const modelText = activeModel ? shortModel(activeModel) : "";
    const mainHint = sending
      ? "Esc to stop · Ctrl+C exit"
      : showCommandPicker
        ? "↑↓ choose command · Enter run · Esc close"
        : `? shortcuts · Tab complete · Shift+Tab mode · ${mode.indicator} ${mode.status}`;

    return (
      <Box paddingX={1} paddingBottom={1} justifyContent="space-between">
        <Text color="#555">{fileCountText}</Text>
        <Text color="#555">{mainHint}</Text>
        <Text color={mode.accent}>{modelText}</Text>
      </Box>
    );
  }

  if (currentView === "vault") {
    return (
      <Box paddingX={1} paddingBottom={1}>
        <Text color="#666">j/k move · Enter open folder · Backspace up · Escape back to chat</Text>
      </Box>
    );
  }

  if (currentView === "history") {
    return (
      <Box paddingX={1} paddingBottom={1}>
        <Text color="#666">j/k move · Escape back to chat</Text>
      </Box>
    );
  }

  if (currentView === "sessions") {
    return (
      <Box paddingX={1} paddingBottom={1}>
        <Text color="#666">Enter open · n new session · r rename · d delete · Escape back to chat</Text>
      </Box>
    );
  }

  if (currentView === "providers") {
    return (
      <Box paddingX={1} paddingBottom={1}>
        <Text color="#666">j/k move · Enter act · t toggle · e edit · a add slot · d remove slot · p pin slot · u use local model · r refresh · Escape back to chat</Text>
      </Box>
    );
  }

  return (
    <Box paddingX={1} paddingBottom={1}>
      <Text color="#666">Escape back to chat</Text>
    </Box>
  );
}

function CommandMenu({ matches, selectedIndex }) {
  if (!matches.length) return null;

  return (
    <Box flexDirection="column" paddingX={1} marginTop={1}>
      {matches.map(([cmd, desc], index) => {
        const active = index === selectedIndex;
        return (
          <Text key={cmd} color={active ? "#f5f5f5" : "#888"}>
            {`${active ? "❯" : " "} ${cmd.padEnd(12)} ${desc}`}
          </Text>
        );
      })}
    </Box>
  );
}

function MessageCard({ msg }) {
  const time = formatTime(msg.timestamp);
  const toolCalls = msg.toolCalls ?? [];
  const metaParts = [];

  if (cleanRuntimeValue(msg.backendUsed)) metaParts.push(msg.backendUsed);
  if (cleanRuntimeValue(msg.modelUsed)) metaParts.push(shortModel(msg.modelUsed));

  let title = "[note]";
  let titleColor = "#888";
  let bodyColor = "#d8d8d8";

  if (msg.role === "user") {
    title = "[you]";
    titleColor = "#f5f5f5";
    bodyColor = "#ffffff";
  } else if (msg.role === "assistant") {
    title = "[o] Overseer";
    titleColor = "#e08a22";
  } else if (msg.role === "error") {
    title = "[!] error";
    titleColor = "#d65c4a";
    bodyColor = "#f3d5d2";
  } else if (msg.tone === "warning") {
    title = "[>] note";
    titleColor = "#d0b16a";
  }

  return (
    <Box flexDirection="column" marginBottom={1}>
      <Box justifyContent="space-between">
        <Text color={titleColor} bold>{title}</Text>
        <Text color="#666">{time}</Text>
      </Box>
      {metaParts.length ? <Text color="#666">{metaParts.join("  ·  ")}</Text> : null}
      {toolCalls.length ? (
        <Box>
          <Text color="#888">{summarizeToolCalls(toolCalls)}</Text>
          <Text color="#555">{"  · ctrl+o to expand"}</Text>
        </Box>
      ) : null}
      <Text color={bodyColor} wrap="wrap">{msg.content}</Text>
    </Box>
  );
}

function ChatHomeHint({ currentMode }) {
  const mode = modeConfig(currentMode);
  const prompts = starterPrompts(currentMode);

  return (
    <Box flexDirection="column" paddingTop={1}>
      <Text color="#f5f5f5">{`❯ Try "${prompts[0]}"`}</Text>
      <Text color="#666">{`${mode.indicator} ${mode.composerHint}`}</Text>
    </Box>
  );
}

function PendingRow({ currentMode, sendingStartedAt, tokenTotal }) {
  const mode = modeConfig(currentMode);
  const label = currentMode === "think"
    ? "Thinking…"
    : currentMode === "capture"
      ? "Processing for capture…"
      : "Processing…";

  return (
    <Box flexDirection="column" marginBottom={1}>
      <ThinkingIndicator
        label={label}
        modeAccent={mode.accent}
        startedAt={sendingStartedAt}
        tokenTotal={tokenTotal}
      />
      <Text color="#666">{currentMode === "capture" ? "Capture mode may write durable notes." : "This mode will not write durable notes."}</Text>
    </Box>
  );
}

function ChatView({ messages, sending, currentMode, sendingStartedAt, tokenTotal }) {
  const visibleMessages = messages.slice(-MAX_VISIBLE_MESSAGES);

  return (
    <Box flexDirection="column" flexGrow={1}>
      {!visibleMessages.length ? <ChatHomeHint currentMode={currentMode} /> : null}
      <Box flexDirection="column" marginTop={visibleMessages.length ? 1 : 2}>
        {visibleMessages.map((message) => (
          <MessageCard key={message.id} msg={message} />
        ))}
        {sending ? <PendingRow currentMode={currentMode} sendingStartedAt={sendingStartedAt} tokenTotal={tokenTotal} /> : null}
      </Box>
    </Box>
  );
}

function VaultView({ browser, onMove, onOpen, onUp, onBack, columns }) {
  useInput((input, key) => {
    if (key.escape || input === "q") {
      onBack();
      return;
    }
    if (key.downArrow || input === "j") {
      onMove(1);
      return;
    }
    if (key.upArrow || input === "k") {
      onMove(-1);
      return;
    }
    if (key.leftArrow || key.backspace || input === "h") {
      onUp();
      return;
    }
    if (key.return || key.rightArrow || input === "l") {
      onOpen();
    }
  });

  const selected = browser.entries[browser.cursor];
  const preview = useMemo(() => readVaultPreview(selected), [selected]);
  const wide = columns >= WIDE_VAULT_LAYOUT_MIN;
  const start = Math.max(0, browser.cursor - 8);
  const end = Math.min(browser.entries.length, start + 18);
  const visibleEntries = browser.entries.slice(start, end);

  if (browser.error) {
    return (
      <Box flexDirection="column" paddingTop={1}>
        <Text color="#d65c4a" bold>vault unavailable</Text>
        <Text color="#888">{browser.error}</Text>
      </Box>
    );
  }

  return (
    <Box flexDirection="column" flexGrow={1} paddingTop={1}>
      <Text color="#d8d8d8" bold>VAULT</Text>
      <Text color="#666">{`${formatVaultLocation(browser.root, browser.currentPath)} · ${browser.entries.length} visible entries`}</Text>
      <Box flexGrow={1} marginTop={1} flexDirection={wide ? "row" : "column"}>
        <Box width={wide ? 42 : undefined} marginRight={wide ? 2 : 0} flexDirection="column">
          {browser.entries.length ? visibleEntries.map((entry, offset) => {
            const index = start + offset;
            const selectedRow = index === browser.cursor;
            const prefix = selectedRow ? "❯" : " ";
            const color = selectedRow ? "#f5f5f5" : entry.type === "directory" ? "#e08a22" : "#cfcfcf";
            const suffix = entry.type === "directory" ? "/" : "";
            return (
              <Text key={entry.fullPath} color={color}>{`${prefix} ${truncateText(entry.name + suffix, 34)}${entry.type === "file" ? `  ${formatBytes(entry.size)}` : ""}`}</Text>
            );
          }) : <Text color="#666">No visible files here.</Text>}
        </Box>
        <Box flexGrow={1} flexDirection="column" marginTop={wide ? 0 : 1}>
          <Text color="#888" bold>{selected ? selected.name : "preview"}</Text>
          <Text color="#666">{selected?.type === "directory" ? "directory" : selected ? formatBytes(selected.size) : "-"}</Text>
          <Box marginTop={1} flexDirection="column">
            {preview.split("\n").slice(0, wide ? 32 : 18).map((line, index) => (
              <Text key={`${selected?.fullPath || "preview"}-${index}`} color="#cfcfcf">{line || " "}</Text>
            ))}
          </Box>
        </Box>
      </Box>
    </Box>
  );
}

function HistoryView({ rows, cursor, setCursor, onBack }) {
  useInput((input, key) => {
    if (key.escape || input === "q") {
      onBack();
      return;
    }
    if (key.downArrow || input === "j") {
      setCursor((value) => Math.min(value + 1, Math.max(rows.length - 1, 0)));
      return;
    }
    if (key.upArrow || input === "k") {
      setCursor((value) => Math.max(value - 1, 0));
    }
  });

  const selected = rows[cursor];
  const start = Math.max(0, cursor - 8);
  const end = Math.min(rows.length, start + VISIBLE_HISTORY_ROWS);
  const visibleRows = rows.slice(start, end);

  return (
    <Box flexDirection="column" flexGrow={1} paddingTop={1}>
      <Text color="#d8d8d8" bold>HISTORY</Text>
      <Text color="#666">{`${rows.length} recent messages across local sessions`}</Text>
      <Box flexGrow={1} marginTop={1} flexDirection="row">
        <Box width={48} marginRight={2} flexDirection="column">
          {visibleRows.map((row, offset) => {
            const index = start + offset;
            const selectedRow = index === cursor;
            const prefix = selectedRow ? "❯" : " ";
            const color = selectedRow ? "#f5f5f5" : "#cfcfcf";
            return (
              <Text key={row.id} color={color}>
                {`${prefix} ${truncateText(row.session_title, 20).padEnd(20)} ${truncateText(row.role, 9).padEnd(9)} ${formatDate(row.created_at)}`}
              </Text>
            );
          })}
        </Box>
        <Box flexGrow={1} flexDirection="column">
          <Text color="#888" bold>{selected ? truncateText(selected.session_title, 48) : "preview"}</Text>
          <Text color="#666">{selected ? `${selected.role} · ${formatDate(selected.created_at)} · ${selected.session_mode}` : "-"}</Text>
          <Box marginTop={1} flexDirection="column">
            {(selected?.content || "No history yet.").split("\n").slice(0, 32).map((line, index) => (
              <Text key={`${selected?.id || "history"}-${index}`} color="#cfcfcf">{line || " "}</Text>
            ))}
          </Box>
        </Box>
      </Box>
    </Box>
  );
}

function UsageView({ health, lastBackend, lastModel, onBack }) {
  useInput((input, key) => {
    if (key.escape || input === "q") onBack();
  });

  const runtime = cleanRuntimeValue(runtimeLabel(health, lastBackend)) ?? "rotator";
  const model = currentModelLabel(lastModel, health);
  const ledger = Object.entries(health?.token_ledger ?? {}).sort((left, right) => right[1] - left[1]);
  const blocked = Object.entries(health?.blocked_slots ?? {});
  const nodes = health?.trusted_nodes ?? [];

  return (
    <Box flexDirection="column" flexGrow={1} paddingTop={1}>
      <Text color="#d8d8d8" bold>USAGE</Text>
      <Text color="#666">{`${runtime} · ${model} · ${slotSummary(health)}`}</Text>

      <Box marginTop={1} flexDirection="column">
        <Text color="#888" bold>TOKENS</Text>
        {ledger.length ? ledger.map(([modelName, total]) => (
          <Text key={modelName} color="#cfcfcf">{`${truncateText(modelName, 44).padEnd(44)} ${String(total).padStart(8)}`}</Text>
        )) : <Text color="#666">No token usage yet.</Text>}
      </Box>

      <Box marginTop={1} flexDirection="column">
        <Text color="#888" bold>GATEWAY</Text>
        <Text color="#cfcfcf">{`status    ${health?.backend_status ?? "-"}`}</Text>
        <Text color="#cfcfcf">{`backend   ${health?.backend ?? "-"}`}</Text>
        <Text color="#cfcfcf">{`runtime   ${runtime}`}</Text>
        <Text color="#cfcfcf">{`endpoint  ${shortEndpoint(health?.api_url ?? health?.api_urls?.[0])}`}</Text>
        <Text color="#cfcfcf">{`vault     ${(health?.vault_last_sync || "").slice(0, 10) || "-"}`}</Text>
      </Box>

      <Box marginTop={1} flexDirection="column">
        <Text color="#888" bold>TRUSTED NODES</Text>
        {nodes.length ? nodes.map((node) => (
          <Text key={node.node_id} color="#cfcfcf">{`${truncateText(node.hostname, 18).padEnd(18)} ${truncateText(shortModel(node.models?.[0]) || "unknown", 18).padEnd(18)} ${shortEndpoint(node.inference_url)}`}</Text>
        )) : <Text color="#666">No trusted nodes online.</Text>}
      </Box>

      <Box marginTop={1} flexDirection="column">
        <Text color="#888" bold>BLOCKED SLOTS</Text>
        {blocked.length ? blocked.map(([slotName, until]) => (
          <Text key={slotName} color="#cfcfcf">{`${slotName.padEnd(24)} ${String(until)}`}</Text>
        )) : <Text color="#666">No blocked slots.</Text>}
      </Box>
    </Box>
  );
}

function ProviderView({
  providerState,
  rows,
  cursor,
  onMove,
  onBack,
  onRefresh,
  onToggle,
  onBeginEdit,
  onPin,
  onRemoveOpenrouterModel,
  onApplyPatch,
  editor,
  onEditorChange,
  onEditorSubmit,
  onEditorCancel,
  notice,
  busy,
}) {
  const selected = rows[cursor];
  const configPath = providerState?.config_path || "-";
  const localNodeLabel = providerState?.trusted_node_count
    ? `${providerState.trusted_node_count} nodes · ${(providerState.trusted_node_models || []).slice(0, 3).join(", ")}`
    : "no trusted nodes";

  useInput((input, key) => {
    if (editor) {
      if (key.escape) {
        onEditorCancel();
        return;
      }
      if (key.return) {
        onEditorSubmit();
        return;
      }
      if (key.backspace || key.delete) {
        onEditorChange(editor.value.slice(0, -1));
        return;
      }
      if (!key.ctrl && !key.meta && input) {
        onEditorChange(editor.value + input);
      }
      return;
    }

    if (key.escape || input === "q") {
      onBack();
      return;
    }
    if (key.downArrow || input === "j") {
      onMove(1);
      return;
    }
    if (key.upArrow || input === "k") {
      onMove(-1);
      return;
    }
    if (input === "r") {
      onRefresh();
      return;
    }
    if (input === "t" && selected?.togglePatch) {
      onToggle(selected.togglePatch);
      return;
    }
    if (input === "e" && selected?.editField) {
      onBeginEdit({
        field: selected.editField,
        label: selected.label,
        value: selected.value === "-" ? "" : selected.value,
        submitLabel: selected.help,
      });
      return;
    }
    if (input === "a" && selected?.editor) {
      onBeginEdit({
        field: selected.editor.field,
        label: selected.editor.label,
        value: selected.editor.initialValue,
        submitLabel: selected.editor.submitLabel,
      });
      return;
    }
    if (input === "d" && selected?.removeOpenrouterModel) {
      onRemoveOpenrouterModel(selected.removeOpenrouterModel);
      return;
    }
    if (input === "p" && selected?.pinPatch) {
      onPin(selected.pinPatch);
      return;
    }
    if (input === "u" && selected?.applyPatch) {
      onApplyPatch(selected.applyPatch);
      return;
    }
    if (key.return) {
      if (selected?.defaultAction === "toggle" && selected.togglePatch) {
        onToggle(selected.togglePatch);
        return;
      }
      if (selected?.defaultAction === "edit" && selected.editField) {
        onBeginEdit({
          field: selected.editField,
          label: selected.label,
          value: selected.value === "-" ? "" : selected.value,
          submitLabel: selected.help,
        });
        return;
      }
      if (selected?.defaultAction === "pin" && selected.pinPatch) {
        onPin(selected.pinPatch);
        return;
      }
      if (selected?.defaultAction === "apply" && selected.applyPatch) {
        onApplyPatch(selected.applyPatch);
      }
    }
  });

  return (
    <Box flexDirection="column" flexGrow={1} paddingTop={1}>
      <Text color="#d8d8d8" bold>PROVIDERS</Text>
      <Text color="#666">{`${providerState?.backend || "auto"} · ${localNodeLabel || "no trusted nodes"} · ${busy ? "updating" : "ready"}`}</Text>

      <Box flexGrow={1} marginTop={1} flexDirection="row">
        <Box width={54} marginRight={2} flexDirection="column">
          {rows.length ? rows.map((row, index) => {
            const selectedRow = index === cursor;
            const prefix = selectedRow ? "❯" : " ";
            const color = selectedRow ? "#f5f5f5" : "#cfcfcf";
            return (
              <Text key={row.key} color={color}>
                {`${prefix} ${truncateText(row.label, 18).padEnd(18)} ${truncateText(String(row.value), 30)}`}
              </Text>
            );
          }) : <Text color="#666">No provider state available.</Text>}
        </Box>

        <Box flexGrow={1} flexDirection="column">
          <Text color="#888" bold>{selected?.label || "selection"}</Text>
          <Text color="#666">{selected?.help || "Pick a row to inspect or edit runtime config."}</Text>
          <Box marginTop={1} flexDirection="column">
            <Text color="#cfcfcf">{`config path  ${configPath}`}</Text>
            <Text color="#cfcfcf">{`backends     ${(providerState?.available_backends || []).join(", ") || "auto"}`}</Text>
            <Text color="#cfcfcf">{`local bias   ${providerState?.prefer_local_nodes ? "on" : "off"}`}</Text>
            <Text color="#cfcfcf">{`worker       ${providerState?.worker?.status || "-"}`}</Text>
          </Box>

          {editor ? (
            <Box flexDirection="column" marginTop={2}>
              <Text color="#e08a22" bold>{editor.label}</Text>
              <Text color="#666">{editor.submitLabel}</Text>
              <Text color="#f5f5f5">{`> ${editor.value || ""}▌`}</Text>
            </Box>
          ) : null}

          {notice ? (
            <Box marginTop={2}>
              <Text color={notice.startsWith("error:") ? "#d65c4a" : "#888"}>{notice}</Text>
            </Box>
          ) : null}
        </Box>
      </Box>
    </Box>
  );
}

function BlinkCursor({ color = "#555" }) {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const id = setInterval(() => setVisible((v) => !v), 530);
    return () => clearInterval(id);
  }, []);
  return <Text color={color}>{visible ? "▌" : " "}</Text>;
}

function MultilineComposer({
  currentMode,
  columns,
  value,
  onChange,
  onSubmit,
  onCycleMode,
  onTabComplete,
  onShortcutHelp,
  onStop,
  sending,
  showCommandPicker,
  onCommandMove,
  onCommandAccept,
}) {
  const mode = modeConfig(currentMode);
  const canCompleteCommand = value.startsWith("/") && !value.includes(" ") && !value.includes("\n");
  const lines = value ? value.split("\n") : [""];

  useInput((input, key) => {
    if (sending) {
      if (key.escape) {
        onStop?.();
      }
      return;
    }

    if ((key.tab && key.shift) || input === "\u001b[Z") {
      onCycleMode(1);
      return;
    }

    if (key.tab && canCompleteCommand) {
      onTabComplete();
      return;
    }

    if (showCommandPicker && (key.downArrow || key.upArrow)) {
      onCommandMove(key.downArrow ? 1 : -1);
      return;
    }

    if (!value && input === "?") {
      onShortcutHelp();
      return;
    }

    if (key.return && key.shift) {
      onChange(`${value}\n`);
      return;
    }

    if (key.return) {
      if (showCommandPicker) {
        onCommandAccept();
        return;
      }
      onSubmit(value);
      return;
    }

    if (key.escape && canCompleteCommand) {
      onChange("");
      return;
    }

    if (key.backspace || key.delete) {
      onChange(value.slice(0, -1));
      return;
    }

    if (!key.ctrl && !key.meta && input) {
      onChange(value + input);
    }
  });

  const placeholderText = sending
    ? "processing… (Esc to stop)"
    : currentMode === "capture"
      ? "Capture to vault..."
      : currentMode === "think"
        ? "Think with Overseer..."
        : "";

  return (
    <Box flexDirection="column">
      <Box paddingX={1}>
        <Divider columns={columns} color={mode.accent} />
      </Box>
      <Box paddingX={1}>
        <Text color={mode.accent} bold>{"❯ "}</Text>
        <Box flexDirection="column">
          {!value ? (
            <Box>
              <Text color="#555">{placeholderText}</Text>
              {!sending ? <BlinkCursor color="#444" /> : null}
            </Box>
          ) : (
            <>
              <Text color="#f5f5f5">{`${lines[0]}${lines.length === 1 ? "▌" : ""}`}</Text>
              {lines.slice(1).map((line, index) => {
                const lastLine = index === lines.length - 2;
                return (
                  <Box key={`composer-line-${index}`}>
                    <Text color="#666">{"  "}</Text>
                    <Text color="#f5f5f5">{`${line}${lastLine ? "▌" : ""}`}</Text>
                  </Box>
                );
              })}
            </>
          )}
        </Box>
      </Box>
      <Box paddingX={1}>
        <Divider columns={columns} color={mode.accent} />
      </Box>
    </Box>
  );
}

function App() {
  const { exit } = useApp();
  const { stdout } = useStdout();

  const [phase, setPhase] = useState("intro");
  const [currentView, setCurrentView] = useState("chat");
  const [managerSessions, setManagerSessions] = useState([]);
  const [sessionId, setSessionId] = useState(null);
  const [sessionTitle, setSessionTitle] = useState("new session");
  const [messages, setMessages] = useState([]);
  const [health, setHealth] = useState({});
  const [elapsed, setElapsed] = useState(null);
  const [sending, setSending] = useState(false);
  const [sendingStartedAt, setSendingStartedAt] = useState(null);
  const [vaultFileCount, setVaultFileCount] = useState(null);
  const [currentMode, setCurrentMode] = useState("chat");
  const [inputValue, setInputValue] = useState("");
  const [extractions, setExtractions] = useState([]);
  const [lastBackend, setLastBackend] = useState(null);
  const [lastModel, setLastModel] = useState(null);
  const [lastFallback, setLastFallback] = useState(null);
  const [historyCursor, setHistoryCursor] = useState(0);
  const [vaultBrowser, setVaultBrowser] = useState(() => loadVaultDirectory());
  const [providerState, setProviderState] = useState(null);
  const [providerCursor, setProviderCursor] = useState(0);
  const [providerEditor, setProviderEditor] = useState(null);
  const [providerNotice, setProviderNotice] = useState(null);
  const [providerBusy, setProviderBusy] = useState(false);
  const [selectedCommandIndex, setSelectedCommandIndex] = useState(0);
  const [ctrlCPending, setCtrlCPending] = useState(false);
  const abortRef = useRef(null);
  const afterIntroRef = useRef(null);
  const ctrlCTimerRef = useRef(null);

  const rows = stdout?.rows ?? 40;
  const columns = stdout?.columns ?? 120;
  const commandMatches = useMemo(() => {
    if (!(currentView === "chat" && inputValue.startsWith("/") && !inputValue.includes(" ") && !inputValue.includes("\n"))) {
      return [];
    }
    return COMMANDS.filter(([cmd]) => cmd.startsWith(inputValue)).slice(0, 8);
  }, [currentView, inputValue]);
  const showCmdMenu = commandMatches.length > 0;

  const historyRows = useMemo(() => listRecentMessages(HISTORY_LIMIT), [messages, sessionId, managerSessions.length]);
  const providerRows = useMemo(() => buildProviderRows(providerState), [providerState]);
  const sessionStats = useMemo(() => {
    const totalChars = messages.reduce((sum, message) => sum + (message.content || "").length, 0);
    return {
      messageCount: messages.length,
      totalChars,
      estimatedTokens: Math.round(totalChars / 4),
    };
  }, [messages]);

  useEffect(() => {
    setSelectedCommandIndex(0);
  }, [inputValue]);

  useEffect(() => {
    if (selectedCommandIndex >= commandMatches.length) {
      setSelectedCommandIndex(0);
    }
  }, [commandMatches.length, selectedCommandIndex]);

  function addMsg(message) {
    setMessages((prev) => [...prev, makeMessage(message)]);
  }

  async function loadHealth() {
    try {
      const fresh = await fetchHealth(AbortSignal.timeout(5000));
      setHealth(fresh);
      return fresh;
    } catch (error) {
      const runtime = getApiRuntime();
      const failed = {
        backend: "-",
        backend_status: "unreachable",
        api_url: runtime.resolvedUrl || runtime.defaultUrl,
        api_urls: runtime.configuredUrls,
        api_source: runtime.source,
        connection_error: formatApiError(error),
      };
      setHealth(failed);
      return failed;
    }
  }

  async function loadProviderState() {
    setProviderBusy(true);
    try {
      const fresh = await fetchProviders(AbortSignal.timeout(8000), health.api_url);
      setProviderState(fresh);
      return fresh;
    } catch (error) {
      const detail = `error: ${formatApiError(error)}`;
      setProviderNotice(detail);
      return null;
    } finally {
      setProviderBusy(false);
    }
  }

  async function applyProviderPatch(patch, successLabel) {
    setProviderBusy(true);
    try {
      const updated = await updateProviders(patch, AbortSignal.timeout(8000), health.api_url);
      setProviderState(updated);
      setProviderNotice(successLabel);
      await loadHealth();
      return updated;
    } catch (error) {
      setProviderNotice(`error: ${formatApiError(error)}`);
      return null;
    } finally {
      setProviderBusy(false);
    }
  }

  function refreshVaultBrowser(targetPath, cursor = 0) {
    const next = loadVaultDirectory(targetPath);
    const safeCursor = Math.min(cursor, Math.max(next.entries.length - 1, 0));
    setVaultBrowser({ ...next, cursor: safeCursor });
  }

  function startNewSession() {
    const nextMode = "chat";
    const id = createSession("new session", nextMode);
    setSessionId(id);
    setSessionTitle("new session");
    setCurrentMode(nextMode);
    setCurrentView("chat");
    setMessages([]);
    setExtractions([]);
    setInputValue("");
    setLastFallback(null);
    setManagerSessions(listAllSessions());
    setPhase("ready");
  }

  function resumeSession(session) {
    setSessionId(session.id);
    setSessionTitle(session.title || "untitled");
    setCurrentMode(session.mode || "chat");
    setCurrentView("chat");
    const prior = getMessages(session.id, 80);
    setMessages(prior.map((message) => ({
      id: message.id,
      role: message.role,
      content: message.content,
      toolCalls: message.tool_calls ? JSON.parse(message.tool_calls) : [],
      timestamp: message.created_at,
      backendUsed: null,
      modelUsed: null,
      tone: message.role === "error" ? "error" : "default",
    })));
    setExtractions(getExtractions(session.id));
    setLastFallback(null);
    setInputValue("");
    setPhase("ready");
  }

  function afterIntro() {
    void loadHealth();
    setManagerSessions(listAllSessions());
    setVaultFileCount(countVaultFiles());
    startNewSession();
  }

  afterIntroRef.current = afterIntro;

  useEffect(() => {
    if (phase !== "intro") return;
    setManagerSessions(listAllSessions());
    void loadHealth();
  }, [phase]);

  useEffect(() => {
    if (phase === "intro") return;
    void loadHealth();
  }, [phase]);

  useEffect(() => {
    if (currentView === "vault") {
      refreshVaultBrowser(vaultBrowser.currentPath);
    }
    if (currentView === "usage") {
      void loadHealth();
    }
    if (currentView === "history") {
      setHistoryCursor(0);
    }
    if (currentView === "providers") {
      setProviderCursor(0);
      setProviderEditor(null);
      void loadProviderState();
    }
  }, [currentView]);

  function setSessionMode(modeId) {
    setCurrentMode(modeId);
    if (sessionId) updateSessionMode(sessionId, modeId);
  }

  function cycleMode(step = 1) {
    const index = MODES.findIndex((mode) => mode.id === currentMode);
    const next = MODES[(index + step + MODES.length) % MODES.length];
    setSessionMode(next.id);
  }

  function switchView(viewId) {
    if (viewId === "sessions") {
      setManagerSessions(listAllSessions());
    }
    if (viewId === "vault") {
      refreshVaultBrowser(vaultBrowser.currentPath);
    }
    if (viewId === "history") {
      setHistoryCursor(0);
    }
    if (viewId === "usage") {
      void loadHealth();
    }
    if (viewId === "providers") {
      setProviderCursor(0);
      setProviderEditor(null);
      void loadProviderState();
    }
    setCurrentView(viewId);
  }

  function completeSlashCommand() {
    if (!commandMatches.length) return;
    if (commandMatches.length === 1) {
      setInputValue(`${commandMatches[0][0]} `);
      return;
    }
    setSelectedCommandIndex((value) => (value + 1) % commandMatches.length);
  }

  async function handleSend(text) {
    const trimmed = text.trim();
    if (!trimmed) {
      setInputValue("");
      return;
    }

    if (sending) {
      addMsg({ role: "error", content: "busy, use /stop to cancel", tone: "error" });
      return;
    }

    addMsg({ role: "user", content: trimmed });
    const userMsgId = addMessage(sessionId, "user", trimmed, null);
    setManagerSessions(listAllSessions());

    const existingMsgs = getMessages(sessionId, 2);
    if (existingMsgs.length <= 1) {
      const nextTitle = trimmed.slice(0, 50);
      updateSessionTitle(sessionId, nextTitle);
      setSessionTitle(nextTitle);
    }

    setSending(true);
    setSendingStartedAt(Date.now());
    setElapsed(null);
    const startedAt = Date.now();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const responseData = await sendChat(trimmed, currentMode, controller.signal, health.api_url);
      const duration = (Date.now() - startedAt) / 1000;
      setElapsed(duration);

      const toolCalls = responseData.tool_calls ?? [];
      const responseText = responseData.response || responseData.error || "no response";
      const backendUsed = responseData.backend_used ?? responseData.backend;
      const modelUsed = cleanRuntimeValue(responseData.model_used ?? backendUsed);
      const configured = health.backend ?? "rotator";
      const backendDisplay = cleanRuntimeValue(backendUsed);

      setLastBackend(backendDisplay);
      setLastModel(modelUsed);
      setLastFallback(responseData.fallback_reason ?? null);

      if (backendDisplay && backendDisplay !== configured) {
        addMsg({
          role: "system",
          tone: "warning",
          content: `Overseer switched from ${configured} to ${backendDisplay}${modelUsed ? ` (${shortModel(modelUsed)})` : ""}${responseData.fallback_reason ? ` · ${responseData.fallback_reason}` : ""}`,
        });
      }

      const treatAsError = Boolean(responseData.error) || !backendDisplay;
      const messageRole = treatAsError ? "error" : "assistant";
      addMsg({
        role: messageRole,
        content: responseText,
        toolCalls: treatAsError ? [] : toolCalls,
        backendUsed: backendDisplay,
        modelUsed,
        tone: treatAsError ? "error" : "default",
      });
      addMessage(sessionId, messageRole, responseText, treatAsError ? null : (toolCalls.length ? toolCalls : null));
      setManagerSessions(listAllSessions());

      if (currentMode === "capture" && !treatAsError) {
        writeVaultConversationNote(sessionId, trimmed, responseText);
      }

      void loadHealth();

      void (async () => {
        try {
          if (currentMode !== "capture") return;
          const extraction = await extractText(trimmed, sessionId, currentMode, AbortSignal.timeout(30000), responseData.api_url);
          if (extraction.entities && !extraction.error) {
            addExtraction(sessionId, userMsgId, extraction.entities, extraction.vault_writes ?? []);
            setExtractions(getExtractions(sessionId));
          }
        } catch {
          // Keep chat responsive even when extraction lags or fails.
        }
      })();
    } catch (error) {
      if (error?.name === "AbortError") {
        addMsg({ role: "system", content: "request stopped", tone: "warning" });
      } else {
        setElapsed((Date.now() - startedAt) / 1000);
        addMsg({ role: "error", content: `error: ${formatApiError(error)}`, tone: "error" });
      }
    } finally {
      setSending(false);
      setSendingStartedAt(null);
      abortRef.current = null;
      setInputValue("");
    }
  }

  async function handleCommand(text) {
    const cmd = text.split(" ")[0].toLowerCase();

    if (cmd === "/help") {
      addMsg({
        role: "system",
        content: COMMANDS.map(([command, desc]) => `${command.padEnd(12)} ${desc}`).join("\n"),
      });
      setInputValue("");
      return;
    }

    if (cmd === "/chat") {
      switchView("chat");
      setInputValue("");
      return;
    }

    if (cmd === "/vault") {
      switchView("vault");
      setInputValue("");
      return;
    }

    if (cmd === "/sessions") {
      switchView("sessions");
      setInputValue("");
      return;
    }

    if (cmd === "/history") {
      switchView("history");
      setInputValue("");
      return;
    }

    if (cmd === "/usage") {
      switchView("usage");
      setInputValue("");
      return;
    }

    if (cmd === "/providers" || cmd === "/models") {
      switchView("providers");
      setInputValue("");
      return;
    }

    if (cmd === "/health") {
      const fresh = await loadHealth();
      addMsg({ role: "system", content: formatHealthSummary(fresh) });
      setInputValue("");
      return;
    }

    if (cmd === "/model") {
      const fresh = await loadHealth();
      const ledger = fresh.token_ledger ?? {};
      const ledgerStr = Object.entries(ledger).map(([modelName, total]) => `${modelName}=${total}`).join("  ") || "no usage yet";
      const actual = lastBackend ?? fresh.backend;
      addMsg({
        role: "system",
        content: `mode=${currentMode}  backend=${fresh.backend}  active=${actual}  model=${lastModel ?? "-"}  url=${fresh.api_url ?? fresh.api_urls?.[0] ?? "-"}\ntokens: ${ledgerStr}`,
      });
      setInputValue("");
      return;
    }

    if (cmd === "/quit" || cmd === "/exit") {
      exit();
      return;
    }

    addMsg({ role: "error", content: `unknown command: ${text}`, tone: "error" });
    setInputValue("");
  }

  function moveCommandSelection(delta) {
    if (!commandMatches.length) return;
    setSelectedCommandIndex((value) => {
      const next = value + delta;
      if (next < 0) return commandMatches.length - 1;
      if (next >= commandMatches.length) return 0;
      return next;
    });
  }

  function acceptSelectedCommand() {
    if (!commandMatches.length) return;
    const [command] = commandMatches[selectedCommandIndex] ?? commandMatches[0];
    void handleCommand(command);
  }

  function submitComposer(value) {
    const trimmed = value.trim();
    if (!trimmed) {
      setInputValue("");
      return;
    }
    if (trimmed.startsWith("/") && !value.includes("\n")) {
      if (showCmdMenu) {
        acceptSelectedCommand();
      } else {
        void handleCommand(trimmed);
      }
      return;
    }
    void handleSend(value);
  }

  function moveVaultCursor(delta) {
    setVaultBrowser((prev) => ({
      ...prev,
      cursor: Math.max(0, Math.min(prev.cursor + delta, Math.max(prev.entries.length - 1, 0))),
    }));
  }

  function openSelectedVaultEntry() {
    const selected = vaultBrowser.entries[vaultBrowser.cursor];
    if (!selected || selected.type !== "directory") return;
    refreshVaultBrowser(selected.fullPath);
  }

  function upVaultDirectory() {
    if (!vaultBrowser.root) return;
    if (vaultBrowser.currentPath === vaultBrowser.root) return;
    refreshVaultBrowser(dirname(vaultBrowser.currentPath));
  }

  function moveProviderCursor(delta) {
    setProviderCursor((value) => Math.max(0, Math.min(value + delta, Math.max(providerRows.length - 1, 0))));
  }

  function beginProviderEdit(editor) {
    setProviderEditor(editor);
    setProviderNotice(null);
  }

  async function submitProviderEdit() {
    if (!providerEditor) return;
    const nextValue = providerEditor.value.trim();

    if (providerEditor.field === "openrouter_models_add") {
      const existing = providerState?.providers?.openrouter?.models || [];
      if (!nextValue) {
        setProviderEditor(null);
        return;
      }
      const nextModels = [...existing, nextValue];
      const updated = await applyProviderPatch(
        { openrouter_models: nextModels },
        `added slot: ${nextValue}`,
      );
      if (updated) setProviderEditor(null);
      return;
    }

    const patch = { [providerEditor.field]: nextValue };
    const updated = await applyProviderPatch(patch, `updated ${providerEditor.label}`);
    if (updated) setProviderEditor(null);
  }

  async function removeOpenrouterModel(modelName) {
    const existing = providerState?.providers?.openrouter?.models || [];
    const nextModels = existing.filter((model) => model !== modelName);
    if (!nextModels.length) {
      setProviderNotice("error: openrouter needs at least one slot");
      return;
    }
    const primary = providerState?.providers?.openrouter?.model;
    const patch = { openrouter_models: nextModels };
    if (primary === modelName) patch.openrouter_model = nextModels[0];
    await applyProviderPatch(patch, `removed slot: ${modelName}`);
  }

  useInput((input, key) => {
    if (phase === "intro") return;
    if (key.ctrl && input === "c") {
      if (sending && abortRef.current) {
        abortRef.current.abort();
        return;
      }
      if (ctrlCPending) {
        clearTimeout(ctrlCTimerRef.current);
        exit();
        return;
      }
      setCtrlCPending(true);
      addMsg({ role: "system", content: "Press Ctrl+C again to exit." });
      ctrlCTimerRef.current = setTimeout(() => setCtrlCPending(false), 2000);
      return;
    }
    if (key.escape && sending && abortRef.current) {
      abortRef.current.abort();
    }
  });

  if (phase === "intro") {
    return (
      <IntroBanner
        onDone={() => afterIntroRef.current()}
        columns={columns}
        health={health}
        sessionCount={managerSessions.length}
        cwd={process.cwd()}
      />
    );
  }

  return (
    <Box flexDirection="column" height={rows}>
      <ShellHeader
        currentMode={currentMode}
        currentView={currentView}
        health={health}
        lastBackend={lastBackend}
        lastModel={lastModel}
        cwd={process.cwd()}
      />

      <Box paddingX={1}>
        <Divider columns={columns} color={modeConfig(currentMode).accent} />
      </Box>

      <Box flexGrow={1} paddingX={1} flexDirection="column">
        {currentView === "chat" ? (
          <ChatView messages={messages} sending={sending} currentMode={currentMode} sendingStartedAt={sendingStartedAt} tokenTotal={tokenCount(health)} />
        ) : null}

        {currentView === "vault" ? (
          <VaultView
            browser={vaultBrowser}
            onMove={moveVaultCursor}
            onOpen={openSelectedVaultEntry}
            onUp={upVaultDirectory}
            onBack={() => switchView("chat")}
            columns={columns}
          />
        ) : null}

        {currentView === "sessions" ? (
          <Box flexGrow={1} paddingTop={1}>
            <SessionManager
              initialSessions={managerSessions}
              activeSessionId={sessionId}
              onOpen={(id) => {
                const selected = managerSessions.find((session) => session.id === id);
                if (selected) resumeSession(selected);
              }}
              onNew={() => startNewSession()}
              onBack={() => switchView("chat")}
            />
          </Box>
        ) : null}

        {currentView === "history" ? (
          <HistoryView
            rows={historyRows}
            cursor={historyCursor}
            setCursor={setHistoryCursor}
            onBack={() => switchView("chat")}
          />
        ) : null}

        {currentView === "usage" ? (
          <UsageView
            health={health}
            lastBackend={lastBackend}
            lastModel={lastModel}
            onBack={() => switchView("chat")}
          />
        ) : null}

        {currentView === "providers" ? (
          <ProviderView
            providerState={providerState}
            rows={providerRows}
            cursor={providerCursor}
            onMove={moveProviderCursor}
            onBack={() => switchView("chat")}
            onRefresh={() => void loadProviderState()}
            onToggle={(patch) => void applyProviderPatch(patch, "provider setting updated")}
            onBeginEdit={beginProviderEdit}
            onPin={(patch) => void applyProviderPatch(patch, "provider pin updated")}
            onRemoveOpenrouterModel={(modelName) => void removeOpenrouterModel(modelName)}
            onApplyPatch={(patch) => void applyProviderPatch(patch, "provider model updated")}
            editor={providerEditor}
            onEditorChange={(value) => setProviderEditor((current) => (current ? { ...current, value } : current))}
            onEditorSubmit={() => void submitProviderEdit()}
            onEditorCancel={() => setProviderEditor(null)}
            notice={providerNotice}
            busy={providerBusy}
          />
        ) : null}
      </Box>

      {currentView === "chat" ? (
        <>
          {showCmdMenu ? <CommandMenu matches={commandMatches} selectedIndex={selectedCommandIndex} /> : null}
          <MultilineComposer
            currentMode={currentMode}
            columns={columns}
            value={inputValue}
            onChange={setInputValue}
            onSubmit={submitComposer}
            onCycleMode={cycleMode}
            onTabComplete={completeSlashCommand}
            onShortcutHelp={() => addMsg({ role: "system", content: KEYBINDINGS_TEXT })}
            onStop={() => { if (abortRef.current) abortRef.current.abort(); }}
            sending={sending}
            showCommandPicker={showCmdMenu}
            onCommandMove={moveCommandSelection}
            onCommandAccept={acceptSelectedCommand}
          />
        </>
      ) : null}

      <FooterBar currentView={currentView} currentMode={currentMode} sending={sending} showCommandPicker={showCmdMenu} vaultFileCount={vaultFileCount} activeModel={lastModel} />
    </Box>
  );
}

const arg = process.argv[2];
if (arg) {
  if (arg === "/health") {
    try {
      const data = await fetchHealth(AbortSignal.timeout(5000));
      console.log(formatHealthSummary(data));
    } catch (error) {
      console.error(`error: ${formatApiError(error)}`);
      process.exit(1);
    }
    process.exit(0);
  }

  try {
    const data = await sendChat(process.argv.slice(2).join(" "), "chat", AbortSignal.timeout(90000));
    console.log(data.response || data.error || "no response");
  } catch (error) {
    console.error(`error: ${formatApiError(error)}`);
    process.exit(1);
  }
  process.exit(0);
}

render(<App />, { exitOnCtrlC: false });
