#!/usr/bin/env node
import { API_KEY, API_URL, VERSION } from "./config.js";

function authHeaders() {
  const h = { "Content-Type": "application/json" };
  if (API_KEY) h["Authorization"] = `Bearer ${API_KEY}`;
  return h;
}

// ── one-shot mode ─────────────────────────────────────────────────────────────

const arg = process.argv[2];
if (arg) {
  if (arg === "/health") {
    try {
      const r = await fetch(`${API_URL}/health`, { signal: AbortSignal.timeout(5000) });
      const d = await r.json();
      const tokens = Object.values(d.token_ledger ?? {}).reduce((a, b) => a + b, 0);
      console.log(`status=${d.backend_status}  model=${d.model}  vault=${(d.vault_last_sync || "").slice(0, 10)}  tokens=${tokens}`);
    } catch (e) {
      console.error(`error: ${e.message}`);
      process.exit(1);
    }
    process.exit(0);
  }

  try {
    const r = await fetch(`${API_URL}/chat`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ message: process.argv.slice(2).join(" ") }),
      signal: AbortSignal.timeout(90000),
    });
    const d = await r.json();
    console.log(d.response || d.error || "no response");
  } catch (e) {
    console.error(`error: ${e.message}`);
    process.exit(1);
  }
  process.exit(0);
}

// ── TUI mode ──────────────────────────────────────────────────────────────────

import React, { useState, useEffect, useRef } from "react";
import { render, Box, Text, useInput, useApp, useStdout } from "ink";
import { IntroBanner, Sprite } from "./components/Banner.jsx";
import { StatusBar } from "./components/StatusBar.jsx";
import { ThinkingDots } from "./components/ThinkingDots.jsx";
import { SessionManager } from "./components/SessionManager.jsx";
import { ExtractionLog } from "./components/ExtractionLog.jsx";
import {
  createSession, updateSessionTitle, listAllSessions,
  addMessage, getMessages, addExtraction, getExtractions,
} from "./db.js";
import { fetchHealth, sendChat } from "./api.js";

// ── slash command registry ────────────────────────────────────────────────────

const COMMANDS = [
  ["/clear",     "clear visible chat"],
  ["/extracted", "toggle extraction log"],
  ["/health",    "api status"],
  ["/help",      "show commands"],
  ["/model",     "show current model"],
  ["/quit",      "exit"],
  ["/sessions",  "switch / resume session"],
  ["/stop",      "cancel in-flight request"],
];

function CommandMenu({ input }) {
  const matches = COMMANDS.filter(([cmd]) => cmd.startsWith(input));
  if (!matches.length) return null;
  return (
    <Box flexDirection="column" paddingX={4} paddingBottom={1}>
      {matches.map(([cmd, desc]) => {
        const isExact = cmd === input;
        return (
          <Box key={cmd}>
            <Text color={isExact ? "#e06c00" : "#888"}>{cmd.padEnd(16)}</Text>
            <Text color="#666">{desc}</Text>
          </Box>
        );
      })}
    </Box>
  );
}

// ── sprite header ─────────────────────────────────────────────────────────────

function SpriteHeader({ health, lastBackend }) {
  const backend = health?.backend ?? "-";
  const model = health?.model ?? "-";
  const vault = (health?.vault_last_sync ?? "").slice(0, 10) || "-";
  const status = health?.backend_status ?? "-";
  const isFallback = lastBackend && lastBackend !== backend;
  const backendLabel = isFallback ? `${backend}->${lastBackend}` : backend;

  return (
    <Box paddingX={2} paddingTop={1} paddingBottom={0}>
      <Sprite />
      <Box flexDirection="column" paddingLeft={2} justifyContent="center">
        <Text color="white" bold>{`Overseer v${VERSION}`}</Text>
        <Text color={isFallback ? "#e06c00" : "#aaa"}>{`${backendLabel}/${model}  ${status === "ok" ? "ok" : status}`}</Text>
        <Text color="#666">{`vault ${vault}`}</Text>
      </Box>
    </Box>
  );
}

// ── main app ──────────────────────────────────────────────────────────────────

function App() {
  const { exit } = useApp();
  const { stdout } = useStdout();

  // intro -> sessions | chat
  const [phase, setPhase] = useState("intro");
  const [managerSessions, setManagerSessions] = useState([]);
  const [sessionId, setSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [health, setHealth] = useState({});
  const [elapsed, setElapsed] = useState(null);
  const [sending, setSending] = useState(false);
  const [inputValue, setInputValue] = useState("");
  const [showExtracted, setShowExtracted] = useState(false);
  const [extractions, setExtractions] = useState([]);
  const [lastBackend, setLastBackend] = useState(null);
  const abortRef = useRef(null);
  const afterIntroRef = useRef(null);

  const rows = stdout?.rows ?? 40;
  // Reserve: header(5) + divider(1) + input(3) + status(1) + padding
  const maxMessages = Math.max(4, rows - 12);

  const showCmdMenu = inputValue.startsWith("/") && !inputValue.includes(" ");

  function afterIntro() {
    void loadHealth();
    const existing = listAllSessions();
    if (existing.length > 0) {
      setManagerSessions(existing);
      setPhase("sessions");
    } else {
      startNewSession();
    }
  }

  afterIntroRef.current = afterIntro;

  async function loadHealth() {
    try {
      const h = await fetchHealth(AbortSignal.timeout(5000));
      setHealth(h);
      return h;
    } catch {
      const err = { backend_status: "unreachable" };
      setHealth(err);
      return err;
    }
  }

  function startNewSession() {
    const id = createSession("new session");
    setSessionId(id);
    setMessages([]);
    setExtractions([]);
    setPhase("chat");
  }

  function resumeSession(id) {
    setSessionId(id);
    const prior = getMessages(id, 50);
    setMessages(prior.map((m) => ({
      role: m.role,
      content: m.content,
      toolCalls: m.tool_calls ? JSON.parse(m.tool_calls) : [],
    })));
    setExtractions(getExtractions(id));
    setPhase("chat");
  }

  function addMsg(msg) {
    setMessages((prev) => [...prev, msg]);
  }

  async function handleSend(text) {
    if (sending) {
      addMsg({ role: "error", content: "busy - /stop to cancel" });
      return;
    }

    addMsg({ role: "user", content: text });
    const userMsgId = addMessage(sessionId, "user", text, null);

    const existingMsgs = getMessages(sessionId, 2);
    if (existingMsgs.length <= 1) {
      updateSessionTitle(sessionId, text.slice(0, 50));
    }

    setSending(true);
    setElapsed(null);
    const t0 = Date.now();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const d = await sendChat(text, controller.signal);
      const took = (Date.now() - t0) / 1000;
      setElapsed(took);

      const toolCalls = d.tool_calls ?? [];
      const response = d.response || d.error || "no response";
      const backendUsed = d.backend_used ?? d.backend;
      const configured = health.backend ?? "gemini";
      setLastBackend(backendUsed);
      if (backendUsed && backendUsed !== configured) {
        addMsg({
          role: "system",
          content: `! backend switched: ${configured} -> ${backendUsed}${d.fallback_reason ? ` (${d.fallback_reason})` : ""}`,
        });
      }

      addMsg({ role: "assistant", content: response, toolCalls });
      addMessage(sessionId, "assistant", response, toolCalls.length ? toolCalls : null);

      void (async () => {
        try {
          const r = await fetch(`${API_URL}/extract`, {
            method: "POST",
            headers: authHeaders(),
            body: JSON.stringify({ text, session_id: sessionId }),
            signal: AbortSignal.timeout(30000),
          });
          if (r.ok) {
            const ex = await r.json();
            if (ex.entities && !ex.error) {
              addExtraction(sessionId, userMsgId, ex.entities, ex.vault_writes ?? []);
              setExtractions(getExtractions(sessionId));
            }
          }
        } catch {}
      })();

    } catch (e) {
      if (e.name === "AbortError") {
        addMsg({ role: "assistant", content: "stopped." });
      } else {
        setElapsed((Date.now() - t0) / 1000);
        addMsg({ role: "error", content: `error: ${e.message}` });
      }
    } finally {
      setSending(false);
      abortRef.current = null;
    }
  }

  async function handleCommand(text) {
    const cmd = text.split(" ")[0].toLowerCase();

    if (cmd === "/help") {
      addMsg({ role: "system", content: COMMANDS.map(([c, d]) => `${c.padEnd(16)}${d}`).join("\n") });
    } else if (cmd === "/clear") {
      setMessages([]);
    } else if (cmd === "/stop") {
      if (abortRef.current) {
        abortRef.current.abort();
      } else {
        addMsg({ role: "system", content: "nothing running" });
      }
    } else if (cmd === "/extracted") {
      setShowExtracted((v) => !v);
    } else if (cmd === "/sessions") {
      setManagerSessions(listAllSessions());
      setPhase("sessions");
    } else if (cmd === "/health") {
      const h = await loadHealth();
      const tokens = Object.values(h.token_ledger ?? {}).reduce((a, b) => a + b, 0);
      addMsg({ role: "system", content: `status=${h.backend_status}  model=${h.model}  vault=${(h.vault_last_sync || "").slice(0, 10)}  tokens=${tokens}` });
    } else if (cmd === "/model") {
      const h = await loadHealth();
      const ledger = h.token_ledger ?? {};
      const ledgerStr = Object.entries(ledger).map(([m, t]) => `${m}=${t}`).join("  ") || "no usage yet";
      const actual = lastBackend ?? h.backend;
      addMsg({ role: "system", content: `backend=${h.backend}  active=${actual}  model=${h.model}\ntokens: ${ledgerStr}` });
    } else if (cmd === "/quit" || cmd === "/exit") {
      exit();
    } else {
      addMsg({ role: "error", content: `unknown: ${text}  - /help` });
    }
  }

  useInput((input, key) => {
    if (phase === "intro") return;
    if (phase !== "chat") return;

    if (key.return) {
      const text = inputValue.trim();
      setInputValue("");
      if (!text) return;
      if (text.startsWith("/")) {
        void handleCommand(text);
      } else {
        void handleSend(text);
      }
      return;
    }

    if (key.tab && showCmdMenu) {
      const matches = COMMANDS.filter(([cmd]) => cmd.startsWith(inputValue));
      if (matches.length === 1) {
        setInputValue(matches[0][0] + " ");
      } else if (matches.length > 1) {
        const idx = matches.findIndex(([cmd]) => cmd === inputValue);
        setInputValue(matches[(idx + 1) % matches.length][0]);
      }
      return;
    }

    if (key.escape) {
      if (showCmdMenu) setInputValue("");
      return;
    }

    if (key.backspace || key.delete) {
      setInputValue((v) => v.slice(0, -1));
      return;
    }

    if (key.ctrl && input === "l") { setMessages([]); return; }
    if (key.ctrl && input === "c") { exit(); return; }

    if (!key.ctrl && !key.meta && input) {
      setInputValue((v) => v + input);
    }
  });

  // ── intro phase ──────────────────────────────────────────────────────────────
  if (phase === "intro") {
    return <IntroBanner onDone={() => afterIntroRef.current()} />;
  }

  // ── sessions picker ──────────────────────────────────────────────────────────
  if (phase === "sessions") {
    return (
      <Box flexDirection="column">
        <SpriteHeader health={health} lastBackend={lastBackend} />
        <SessionManager
          initialSessions={managerSessions}
          activeSessionId={sessionId}
          onOpen={(id) => resumeSession(id)}
          onNew={() => startNewSession()}
          onBack={sessionId ? () => setPhase("chat") : null}
        />
      </Box>
    );
  }

  // ── chat ─────────────────────────────────────────────────────────────────────
  const visibleMessages = messages.slice(-maxMessages);

  return (
    <Box flexDirection="column" height={rows}>
      <SpriteHeader health={health} lastBackend={lastBackend} />

      {/* divider */}
      <Box paddingX={2} paddingTop={1}>
        <Text color="#444">{"─".repeat(Math.max(10, (stdout?.columns ?? 80) - 4))}</Text>
      </Box>

      {/* chat area */}
      <Box flexDirection="column" flexGrow={1} paddingX={2} justifyContent="flex-end" overflow="hidden">
        {visibleMessages.map((m, i) => (
          <MessageRow key={i} msg={m} />
        ))}
        {sending && (
          <Box paddingLeft={7}>
            <ThinkingDots />
          </Box>
        )}
      </Box>

      {/* panels */}
      {showExtracted && <ExtractionLog extractions={extractions} />}
      {showCmdMenu && <CommandMenu input={inputValue} />}

      {/* divider above input */}
      <Box paddingX={2}>
        <Text color="#444">{"─".repeat(Math.max(10, (stdout?.columns ?? 80) - 4))}</Text>
      </Box>

      {/* input */}
      <Box paddingX={2} paddingTop={0} height={2}>
        <Text color="#e06c00">{"  > "}</Text>
        <Text color="white">{inputValue}</Text>
        <Text color="#e06c00">{"▌"}</Text>
      </Box>

      <StatusBar health={health} elapsed={elapsed} lastBackend={lastBackend} />
    </Box>
  );
}

function MessageRow({ msg }) {
  const { role, content, toolCalls = [] } = msg;

  if (role === "user") {
    return (
      <Box marginTop={1}>
        <Text color="white" bold wrap="wrap">{`you    ${content}`}</Text>
      </Box>
    );
  }

  if (role === "assistant") {
    return (
      <Box flexDirection="column" marginBottom={1}>
        {(toolCalls || []).slice(0, 8).map((t, i) => (
          <Text key={i} color="#888">{`  -> ${t}`}</Text>
        ))}
        <Text color="#aaa" wrap="wrap">{`⬡      ${content}`}</Text>
      </Box>
    );
  }

  if (role === "error") {
    return <Text color="#ff6b6b">{content}</Text>;
  }

  // system messages
  return <Text color="#888">{content}</Text>;
}

render(<App />);
