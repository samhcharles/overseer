"""
Overseer API — vault-connected AI gateway.
Supports two backends via OVERSEER_BACKEND env var:
  groq   → Groq API (default, free tier, for now)
  ollama → Ollama (switch to this when Oracle A1 is ready)
"""
import asyncio
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

VAULT_PATH = Path(os.environ.get("VAULT_PATH", "/home/ubuntu/vault"))
OVERSEER_BACKEND = os.environ.get("OVERSEER_BACKEND", "groq")

# Groq settings
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_BASE = "https://api.groq.com/openai/v1"

# Ollama settings (used when backend=ollama, e.g. on Oracle A1)
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")

app = FastAPI(title="Overseer API", version="1.0.0")

token_ledger: dict[str, int] = {}


# ─── vault tools ──────────────────────────────────────────────────────────────

def vault_read(path: str) -> str:
    full = VAULT_PATH / path.lstrip("/")
    if not full.exists():
        return f"[not found: {path}]"
    return full.read_text()


def vault_write(path: str, content: str, commit_msg: str | None = None) -> str:
    full = VAULT_PATH / path.lstrip("/")
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    msg = commit_msg or f"overseer: update {path}"
    try:
        git = ["git", "-C", str(VAULT_PATH)]
        subprocess.run(git + ["config", "--global", "--add", "safe.directory", str(VAULT_PATH)], capture_output=True)
        subprocess.run(git + ["config", "user.email", "overseer@brain"], capture_output=True)
        subprocess.run(git + ["config", "user.name", "Overseer"], capture_output=True)
        subprocess.run(git + ["add", str(full)], check=True, capture_output=True)
        subprocess.run(git + ["commit", "-m", msg], check=True, capture_output=True)
        subprocess.run(git + ["push", "origin", "main"], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        return f"wrote {path} but git error: {e.stderr.decode()[:200]}"
    return f"wrote and committed {path}"


def vault_search(query: str, max_results: int = 10) -> str:
    result = subprocess.run(
        ["rg", "--ignore-case", "--max-count=3", "--with-filename", query, str(VAULT_PATH)],
        capture_output=True, text=True
    )
    lines = result.stdout.strip().splitlines()[:max_results]
    return "\n".join(lines) if lines else "[no results]"


def list_notes(folder: str) -> str:
    full = VAULT_PATH / folder.lstrip("/")
    if not full.is_dir():
        return f"[not a directory: {folder}]"
    files = [str(p.relative_to(VAULT_PATH)) for p in sorted(full.rglob("*.md"))]
    return "\n".join(files) if files else "[empty]"


def web_search(query: str) -> str:
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
        lines = [f"**{r.get('title','')}**\n{r.get('body','')}\n{r.get('href','')}" for r in results]
        return "\n\n".join(lines) if lines else "[no results]"
    except Exception as e:
        return f"[search error: {e}]"


TOOLS_MAP = {
    "vault_read": vault_read,
    "vault_write": vault_write,
    "vault_search": vault_search,
    "list_notes": list_notes,
    "web_search": web_search,
}

TOOLS_SPEC = [
    {"type": "function", "function": {
        "name": "vault_read",
        "description": "Read a file from the vault. Path relative to vault root.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "vault_write",
        "description": "Write content to a vault file and commit+push.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    }},
    {"type": "function", "function": {
        "name": "vault_search",
        "description": "Search vault using ripgrep. Returns matching lines with file paths.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "list_notes",
        "description": "List all markdown files in a vault folder.",
        "parameters": {"type": "object", "properties": {"folder": {"type": "string"}}, "required": ["folder"]},
    }},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web via DuckDuckGo. Returns top 5 results.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    }},
]


# ─── backend-agnostic chat ────────────────────────────────────────────────────

async def _groq_chat(messages: list[dict]) -> dict:
    payload = {"model": GROQ_MODEL, "messages": messages, "tools": TOOLS_SPEC, "tool_choice": "auto"}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{GROQ_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json=payload,
        )
        if not r.is_success:
            raise RuntimeError(f"Groq {r.status_code}: {r.text[:500]}")
        data = r.json()
        usage = data.get("usage", {})
        token_ledger[GROQ_MODEL] = token_ledger.get(GROQ_MODEL, 0) + usage.get("total_tokens", 0)
        choice = data["choices"][0]["message"]
        return {"content": choice.get("content") or "", "tool_calls": choice.get("tool_calls") or []}


async def _ollama_chat(messages: list[dict]) -> dict:
    payload = {"model": OLLAMA_MODEL, "messages": messages, "tools": TOOLS_SPEC, "stream": False}
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()
        msg = data.get("message", {})
        return {"content": msg.get("content") or "", "tool_calls": msg.get("tool_calls") or []}


async def llm_chat(messages: list[dict]) -> dict:
    if OVERSEER_BACKEND == "ollama":
        return await _ollama_chat(messages)
    return await _groq_chat(messages)


async def run_tool_loop(messages: list[dict]) -> tuple[str, list[str]]:
    tool_log: list[str] = []
    for _ in range(10):
        response = await llm_chat(messages)
        content = response["content"]
        tool_calls = response["tool_calls"]

        if not tool_calls:
            return content, tool_log

        # Groq format: tool_calls is list of {id, type, function: {name, arguments}}
        # Ollama format: list of {function: {name, arguments}}
        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            raw_args = fn.get("arguments", {})
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args

            short_args = ", ".join(f"{k}={repr(v)[:40]}" for k, v in args.items())
            tool_log.append(f"{name}({short_args})")

            tool_fn = TOOLS_MAP.get(name)
            result = tool_fn(**args) if tool_fn else f"[unknown tool: {name}]"

            # Groq needs tool_call_id in the tool response
            tool_msg: dict = {"role": "tool", "content": str(result)[:2000]}
            if "id" in tc:
                tool_msg["tool_call_id"] = tc["id"]
            messages.append(tool_msg)

    return "Max tool iterations reached.", tool_log


# ─── trace ────────────────────────────────────────────────────────────────────

def update_trace(status: str, tool_calls: list[str] | None = None) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    vault_sync = ""
    try:
        r = subprocess.run(["git", "-C", str(VAULT_PATH), "log", "-1", "--format=%ar"], capture_output=True, text=True)
        vault_sync = r.stdout.strip()
    except Exception:
        vault_sync = "unknown"

    backend_label = f"Groq/{GROQ_MODEL}" if OVERSEER_BACKEND == "groq" else f"Ollama/{OLLAMA_MODEL}"
    tool_lines = "\n".join(f"- `{t}`" for t in (tool_calls or [])[-5:]) or "*No tool calls this session.*"

    content = f"""---
tags: [overseer, trace]
---

# Overseer — Live Trace

> This file is written by Overseer during active sessions. Refresh to see current activity.

## Status
- **Last active:** {now}
- **Backend:** {backend_label}
- **Vault sync:** {vault_sync}

## Current session

{status}

## Last tool calls

{tool_lines}
"""
    trace_path = VAULT_PATH / "memory" / "overseer-live.md"
    try:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(content)
    except Exception:
        pass


# ─── system prompt ────────────────────────────────────────────────────────────

def system_prompt() -> str:
    facts_sections = []
    facts_dir = VAULT_PATH / "memory" / "facts"
    if facts_dir.exists():
        for f in sorted(facts_dir.glob("*.md")):
            try:
                facts_sections.append(f"\n### {f.stem}\n{f.read_text()[:600]}")
            except Exception:
                pass
    facts = "".join(facts_sections) or "[none yet]"

    return f"""You are Overseer. You route raw data into the vault and retrieve it on demand. Nothing else.

RULES:
- No greetings. No self-introduction. No "I've noted that". No suggestions. No filler.
- Storing data: do it, then confirm in one line (what + where). Nothing more.
- Answering questions: vault_search first. Return exactly what you find. If nothing: "[not found]".
- Never invent, infer, or pad. Only state what is in the vault.
- Responses are terse. One or two sentences max unless the user explicitly asks for more.

ROUTING — when the user gives you raw data, route it silently:
- New person or relationship → vault_write to wiki/personal/people/[firstname].md
- Birthday or personal fact about someone → append to memory/facts/people.md AND the person note
- Preference or default setting → append to memory/facts/preferences.md
- Recurring schedule or event → append to memory/facts/recurring.md
- Project info → vault_write to wiki/projects/[project-name].md
- System or infra info → vault_write to wiki/systems/[name].md
- Any other raw note → vault_write to inbox/yap/[slug].md

RECALL — before answering any question about stored data, always vault_search first.

Current memory facts:
{facts}

Today: {datetime.now().strftime("%Y-%m-%d, %A")}
"""


# ─── request models ───────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None


class TriageRequest(BaseModel):
    content: str
    source: str = "inbox"


class RememberRequest(BaseModel):
    fact: str
    category: str = "preferences"


# ─── endpoints ────────────────────────────────────────────────────────────────

@app.get("/")
async def dashboard():
    return FileResponse(Path(__file__).parent / "index.html")


@app.get("/health")
async def health():
    backend_status = "unknown"
    try:
        if OVERSEER_BACKEND == "groq":
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{GROQ_BASE}/models", headers={"Authorization": f"Bearer {GROQ_API_KEY}"})
                backend_status = "ok" if r.status_code == 200 else f"http {r.status_code}"
        else:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{OLLAMA_URL}/api/tags")
                backend_status = "ok" if r.status_code == 200 else "unreachable"
    except Exception as e:
        backend_status = f"error: {e}"

    vault_sync = ""
    try:
        r = subprocess.run(["git", "-C", str(VAULT_PATH), "log", "-1", "--format=%ci"], capture_output=True, text=True)
        vault_sync = r.stdout.strip()
    except Exception:
        pass

    return {
        "backend": OVERSEER_BACKEND,
        "model": GROQ_MODEL if OVERSEER_BACKEND == "groq" else OLLAMA_MODEL,
        "backend_status": backend_status,
        "vault_path": str(VAULT_PATH),
        "vault_last_sync": vault_sync,
        "token_ledger": token_ledger,
    }


def flush_token_ledger() -> None:
    usage_path = VAULT_PATH / "memory" / "usage.md"
    try:
        usage_path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [f"| {model} | {tokens} | {now} |" for model, tokens in token_ledger.items()]
        header = "| Model | Total Tokens | Last Updated |\n|---|---|---|\n"
        usage_path.write_text(f"# Token Usage\n\n{header}" + "\n".join(lines) + "\n")
    except Exception:
        pass


@app.post("/chat")
async def chat(req: ChatRequest):
    try:
        update_trace(f"Processing: {req.message[:80]}...")
        messages = [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": req.message},
        ]
        answer, tool_log = await run_tool_loop(messages)
        flush_token_ledger()
        update_trace(f"Done: {req.message[:60]}", tool_log)
        return {"response": answer, "tool_calls": tool_log, "backend": OVERSEER_BACKEND}
    except Exception as e:
        import traceback
        return {"response": None, "error": str(e), "trace": traceback.format_exc()[-1000:], "backend": OVERSEER_BACKEND}


@app.post("/triage")
async def triage(req: TriageRequest):
    triage_prompt = f"""Triage this inbox item from source: {req.source}

Return ONLY valid JSON:
{{
  "tags": ["string"],
  "destination": "wiki/orinadus | wiki/madhouse | wiki/personal | wiki/systems | wiki/sessions | wiki/personal/people | daily | ignore",
  "summary": "one sentence",
  "entities": {{
    "people": [],
    "dates": [],
    "projects": [],
    "facts": []
  }}
}}

Content:
{req.content[:3000]}"""

    messages = [{"role": "user", "content": triage_prompt}]
    response = await llm_chat(messages)
    raw = response["content"]

    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        result = json.loads(raw[start:end])
    except Exception:
        result = {
            "tags": ["inbox"],
            "destination": "wiki/personal",
            "summary": req.content[:100],
            "entities": {"people": [], "dates": [], "projects": [], "facts": []},
        }
    return result


@app.post("/remember")
async def remember(req: RememberRequest):
    valid = {"people", "preferences", "recurring"}
    category = req.category if req.category in valid else "preferences"
    facts_path = f"memory/facts/{category}.md"
    existing = vault_read(facts_path)
    today = datetime.now().strftime("%Y-%m-%d")
    updated = existing.rstrip() + f"\n- **{req.fact}** (added {today})\n"
    result = vault_write(facts_path, updated, f"overseer: remember [{category}]")
    return {"stored": req.fact, "category": category, "path": facts_path, "result": result}


@app.get("/recall")
async def recall(q: str = Query(...)):
    results = vault_search(q)
    return {"query": q, "results": results}


@app.get("/trace")
async def trace():
    async def gen():
        trace_path = VAULT_PATH / "memory" / "overseer-live.md"
        last_mtime = 0.0
        for _ in range(120):
            try:
                mtime = trace_path.stat().st_mtime
                if mtime != last_mtime:
                    last_mtime = mtime
                    content = trace_path.read_text()
                    yield f"data: {json.dumps({'content': content})}\n\n"
            except Exception:
                pass
            await asyncio.sleep(2)
    return StreamingResponse(gen(), media_type="text/event-stream")
