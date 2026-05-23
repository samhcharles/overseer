"""
Overseer Gateway — always-on VPS router.
No vault. No model. Just routing, thread persistence, and node registry.

Nodes register via Tailscale. Users reach this via HTTPS (Traefik).
"""
import asyncio
import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

GATEWAY_API_KEY = os.environ.get("GATEWAY_API_KEY", "")
NODE_SECRET = os.environ.get("NODE_SECRET", "")
STATE_DIR = Path(os.environ.get("OVERSEER_STATE_DIR", "/data/overseer"))
DB_PATH = STATE_DIR / "overseer.db"
NODE_TTL = int(os.environ.get("NODE_TTL", "60"))  # seconds before node is stale

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("gateway")

app = FastAPI(title="Overseer Gateway", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── database ─────────────────────────────────────────────────────────────────

def _init_db() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS threads (
                id TEXT NOT NULL,
                turn INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                ts TEXT NOT NULL,
                PRIMARY KEY (id, turn)
            );
            CREATE TABLE IF NOT EXISTS nodes (
                node_id TEXT PRIMARY KEY,
                hostname TEXT,
                inference_url TEXT NOT NULL,
                capabilities TEXT NOT NULL,
                models TEXT NOT NULL,
                has_vault INTEGER NOT NULL DEFAULT 0,
                last_seen REAL NOT NULL
            );
        """)


@contextmanager
def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ─── thread store ──────────────────────────────────────────────────────────────

def thread_load(thread_id: str, limit: int = 30) -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT role, content FROM threads WHERE id=? ORDER BY turn DESC LIMIT ?",
            (thread_id, limit),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def thread_append(thread_id: str, role: str, content: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _db() as conn:
        next_turn = (conn.execute(
            "SELECT COALESCE(MAX(turn), -1) FROM threads WHERE id=?", (thread_id,)
        ).fetchone()[0] or -1) + 1
        conn.execute(
            "INSERT INTO threads (id, turn, role, content, ts) VALUES (?,?,?,?,?)",
            (thread_id, next_turn, role, content, now),
        )


def thread_clear(thread_id: str) -> None:
    with _db() as conn:
        conn.execute("DELETE FROM threads WHERE id=?", (thread_id,))


def thread_list() -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, COUNT(*) AS turns, MAX(ts) AS last_ts FROM threads GROUP BY id ORDER BY last_ts DESC"
        ).fetchall()
    return [{"thread_id": r["id"], "turns": r["turns"], "last_active": r["last_ts"]} for r in rows]


# ─── node registry ─────────────────────────────────────────────────────────────

@dataclass
class Node:
    node_id: str
    hostname: str
    inference_url: str
    capabilities: list[str]
    models: list[str]
    has_vault: bool
    last_seen: float = field(default_factory=time.monotonic)

    def is_alive(self) -> bool:
        return time.monotonic() - self.last_seen < NODE_TTL

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "hostname": self.hostname,
            "inference_url": self.inference_url,
            "capabilities": self.capabilities,
            "models": self.models,
            "has_vault": self.has_vault,
            "age_seconds": int(time.monotonic() - self.last_seen),
            "alive": self.is_alive(),
        }


_nodes: dict[str, Node] = {}


def _load_nodes_from_db() -> None:
    with _db() as conn:
        rows = conn.execute("SELECT * FROM nodes").fetchall()
    for r in rows:
        _nodes[r["node_id"]] = Node(
            node_id=r["node_id"],
            hostname=r["hostname"] or "",
            inference_url=r["inference_url"],
            capabilities=json.loads(r["capabilities"]),
            models=json.loads(r["models"]),
            has_vault=bool(r["has_vault"]),
            last_seen=0.0,  # stale until heartbeat
        )


def _save_node(node: Node) -> None:
    with _db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO nodes
               (node_id, hostname, inference_url, capabilities, models, has_vault, last_seen)
               VALUES (?,?,?,?,?,?,?)""",
            (
                node.node_id, node.hostname, node.inference_url,
                json.dumps(node.capabilities), json.dumps(node.models),
                1 if node.has_vault else 0, time.time(),
            ),
        )


def best_node(prefer_vault: bool = True) -> Node | None:
    alive = [n for n in _nodes.values() if n.is_alive()]
    if not alive:
        return None
    if prefer_vault:
        with_vault = [n for n in alive if n.has_vault]
        if with_vault:
            return sorted(with_vault, key=lambda n: n.last_seen, reverse=True)[0]
    return sorted(alive, key=lambda n: n.last_seen, reverse=True)[0]


# ─── auth ──────────────────────────────────────────────────────────────────────

def _check_api_key(request: Request) -> None:
    if not GATEWAY_API_KEY:
        return  # open if not configured (useful in dev)
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth[7:] != GATEWAY_API_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")


def _check_node_secret(secret: str) -> None:
    if not NODE_SECRET:
        return
    if secret != NODE_SECRET:
        raise HTTPException(status_code=401, detail="invalid node secret")


# ─── request models ────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    thread_id: str = "default"


class NodeRegisterRequest(BaseModel):
    node_id: str
    hostname: str
    inference_url: str
    secret: str
    capabilities: list[str] = ["chat"]
    models: list[str] = []
    has_vault: bool = False


class NodeHeartbeatRequest(BaseModel):
    node_id: str
    secret: str
    models: list[str] | None = None


# ─── chat routing ──────────────────────────────────────────────────────────────

async def _forward_to_node(node: Node, message: str, history: list[dict]) -> dict:
    payload = {"message": message, "history": history}
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(
            f"{node.inference_url.rstrip('/')}/infer/chat",
            json=payload,
            headers={"X-Node-Secret": NODE_SECRET} if NODE_SECRET else {},
        )
        r.raise_for_status()
        return r.json()


# ─── endpoints ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    _init_db()
    _load_nodes_from_db()
    log.info("Gateway started. DB: %s", DB_PATH)


@app.get("/")
async def root():
    alive = [n for n in _nodes.values() if n.is_alive()]
    return HTMLResponse(content=f"""<!DOCTYPE html>
<html><head><title>Overseer</title>
<style>body{{font-family:monospace;background:#0d0d0d;color:#e0e0e0;max-width:700px;margin:2rem auto;padding:1rem}}
input{{width:100%;padding:0.5rem;background:#1a1a1a;border:1px solid #333;color:#e0e0e0;font-family:monospace}}
#log{{white-space:pre-wrap;margin-top:1rem;min-height:400px;background:#111;padding:1rem;overflow-y:auto;max-height:70vh}}
button{{padding:0.5rem 1rem;background:#2a2a2a;border:1px solid #444;color:#ccc;cursor:pointer}}</style>
</head><body>
<h2>Overseer</h2>
<p>{len(alive)} node(s) online</p>
<div id="log">&gt; ready</div>
<input id="inp" type="text" placeholder="say something..." autofocus>
<button onclick="send()">send</button>
<script>
const tid = 'browser-' + Math.random().toString(36).slice(2,8);
async function send(){{
  const msg = document.getElementById('inp').value.trim();
  if(!msg) return;
  document.getElementById('inp').value='';
  const log=document.getElementById('log');
  log.textContent += '\\n> ' + msg;
  const r = await fetch('/chat', {{method:'POST',headers:{{'Content-Type':'application/json','Authorization':'Bearer {GATEWAY_API_KEY}'}},body:JSON.stringify({{message:msg,thread_id:tid}})}});
  const d = await r.json();
  log.textContent += '\\n' + (d.response || d.error || JSON.stringify(d));
  log.scrollTop = log.scrollHeight;
}}
document.getElementById('inp').addEventListener('keydown',e=>{{if(e.key==='Enter')send()}});
</script></body></html>""")


@app.get("/health")
async def health():
    alive = [n.to_dict() for n in _nodes.values() if n.is_alive()]
    stale = [n.to_dict() for n in _nodes.values() if not n.is_alive()]
    return {
        "status": "ok",
        "nodes_alive": len(alive),
        "nodes_stale": len(stale),
        "nodes": alive + stale,
    }


@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    _check_api_key(request)
    node = best_node(prefer_vault=True)
    if not node:
        return {
            "response": "No nodes online. Start Overseer on your laptop or iPhone.",
            "thread_id": req.thread_id,
            "node": None,
        }

    history = thread_load(req.thread_id)
    try:
        result = await _forward_to_node(node, req.message, history)
    except Exception as e:
        log.warning("Node %s failed: %s", node.node_id, e)
        return {
            "response": f"Node unreachable ({node.hostname}): {e}",
            "thread_id": req.thread_id,
            "node": node.node_id,
        }

    content = result.get("content") or result.get("response") or ""
    thread_append(req.thread_id, "user", req.message)
    thread_append(req.thread_id, "assistant", content)

    return {
        "response": content,
        "tool_calls": result.get("tool_calls", []),
        "thread_id": req.thread_id,
        "node": node.node_id,
        "model": result.get("model"),
    }


@app.post("/nodes/register")
async def register_node(req: NodeRegisterRequest, request: Request):
    _check_node_secret(req.secret)
    node = Node(
        node_id=req.node_id,
        hostname=req.hostname,
        inference_url=req.inference_url,
        capabilities=req.capabilities,
        models=req.models,
        has_vault=req.has_vault,
        last_seen=time.monotonic(),
    )
    _nodes[req.node_id] = node
    _save_node(node)
    log.info("Node registered: %s @ %s (vault=%s)", req.node_id, req.hostname, req.has_vault)
    return {"registered": True, "node": node.to_dict()}


@app.post("/nodes/heartbeat")
async def heartbeat(req: NodeHeartbeatRequest):
    _check_node_secret(req.secret)
    node = _nodes.get(req.node_id)
    if not node:
        raise HTTPException(status_code=404, detail="node not registered")
    node.last_seen = time.monotonic()
    if req.models is not None:
        node.models = req.models
    _save_node(node)
    return {"ok": True, "node_id": req.node_id}


@app.get("/nodes")
async def list_nodes(request: Request):
    _check_api_key(request)
    return {"nodes": [n.to_dict() for n in _nodes.values()]}


@app.get("/threads")
async def list_threads(request: Request):
    _check_api_key(request)
    return {"threads": thread_list()}


@app.get("/threads/{thread_id}")
async def get_thread(thread_id: str, request: Request):
    _check_api_key(request)
    history = thread_load(thread_id, limit=100)
    return {"thread_id": thread_id, "turns": len(history), "history": history}


@app.delete("/threads/{thread_id}")
async def clear_thread_endpoint(thread_id: str, request: Request):
    _check_api_key(request)
    thread_clear(thread_id)
    return {"cleared": thread_id}
