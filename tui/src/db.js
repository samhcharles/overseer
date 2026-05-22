import Database from "better-sqlite3";
import { randomUUID } from "crypto";
import { mkdirSync } from "fs";
import { dirname } from "path";
import { DB_PATH } from "./config.js";

let _db;

export function db() {
  if (_db) return _db;
  mkdirSync(dirname(DB_PATH), { recursive: true });
  _db = new Database(DB_PATH);
  _db.pragma("journal_mode = WAL");
  _db.exec(`
    CREATE TABLE IF NOT EXISTS sessions (
      id TEXT PRIMARY KEY,
      title TEXT,
      mode TEXT DEFAULT 'chat',
      created_at INTEGER,
      updated_at INTEGER
    );
    CREATE TABLE IF NOT EXISTS messages (
      id TEXT PRIMARY KEY,
      session_id TEXT REFERENCES sessions(id),
      role TEXT,
      content TEXT,
      tool_calls TEXT,
      created_at INTEGER
    );
    CREATE TABLE IF NOT EXISTS extractions (
      id TEXT PRIMARY KEY,
      session_id TEXT,
      message_id TEXT,
      entities TEXT,
      vault_writes TEXT,
      created_at INTEGER
    );
  `);
  try {
    _db.exec("ALTER TABLE sessions ADD COLUMN mode TEXT DEFAULT 'chat';");
  } catch {}
  return _db;
}

export function createSession(title, mode = "chat") {
  const id = randomUUID();
  const now = Date.now();
  db().prepare("INSERT INTO sessions (id, title, mode, created_at, updated_at) VALUES (?, ?, ?, ?, ?)").run(id, title, mode, now, now);
  return id;
}

export function updateSessionTitle(id, title) {
  db().prepare("UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?").run(title, Date.now(), id);
}

export function updateSessionMode(id, mode) {
  db().prepare("UPDATE sessions SET mode = ?, updated_at = ? WHERE id = ?").run(mode, Date.now(), id);
}

export function touchSession(id) {
  db().prepare("UPDATE sessions SET updated_at = ? WHERE id = ?").run(Date.now(), id);
}

export function listSessions(limit = 5) {
  return db().prepare("SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?").all(limit);
}

export function listAllSessions() {
  return db().prepare("SELECT * FROM sessions ORDER BY updated_at DESC").all();
}

export function getSessionStats(sessionId) {
  const msgs = db().prepare("SELECT role, content FROM messages WHERE session_id = ?").all(sessionId);
  const totalChars = msgs.reduce((a, m) => a + (m.content || "").length, 0);
  return {
    messageCount: msgs.length,
    userMessages: msgs.filter((m) => m.role === "user").length,
    totalChars,
    estimatedTokens: Math.round(totalChars / 4),
  };
}

export function deleteSession(id) {
  db().prepare("DELETE FROM extractions WHERE session_id = ?").run(id);
  db().prepare("DELETE FROM messages WHERE session_id = ?").run(id);
  db().prepare("DELETE FROM sessions WHERE id = ?").run(id);
}

export function renameSession(id, title) {
  db().prepare("UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?").run(title, Date.now(), id);
}

export function addMessage(sessionId, role, content, toolCalls) {
  const id = randomUUID();
  db()
    .prepare("INSERT INTO messages (id, session_id, role, content, tool_calls, created_at) VALUES (?, ?, ?, ?, ?, ?)")
    .run(id, sessionId, role, content, toolCalls ? JSON.stringify(toolCalls) : null, Date.now());
  touchSession(sessionId);
  return id;
}

export function getMessages(sessionId, limit = 10) {
  return db()
    .prepare("SELECT * FROM messages WHERE session_id = ? ORDER BY created_at DESC LIMIT ?")
    .all(sessionId, limit)
    .reverse();
}

export function listRecentMessages(limit = 100) {
  return db()
    .prepare(`
      SELECT
        m.id,
        m.session_id,
        m.role,
        m.content,
        m.tool_calls,
        m.created_at,
        COALESCE(s.title, 'untitled') AS session_title,
        COALESCE(s.mode, 'chat') AS session_mode
      FROM messages m
      LEFT JOIN sessions s ON s.id = m.session_id
      ORDER BY m.created_at DESC
      LIMIT ?
    `)
    .all(limit);
}

export function addExtraction(sessionId, messageId, entities, vaultWrites) {
  const id = randomUUID();
  db()
    .prepare("INSERT INTO extractions (id, session_id, message_id, entities, vault_writes, created_at) VALUES (?, ?, ?, ?, ?, ?)")
    .run(id, sessionId, messageId, JSON.stringify(entities), JSON.stringify(vaultWrites), Date.now());
}

export function getExtractions(sessionId) {
  return db().prepare("SELECT * FROM extractions WHERE session_id = ? ORDER BY created_at ASC").all(sessionId);
}
