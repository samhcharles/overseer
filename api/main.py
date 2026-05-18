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
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

VAULT_PATH = Path(os.environ.get("VAULT_PATH", "/home/ubuntu/vault"))
OVERSEER_BACKEND = os.environ.get("OVERSEER_BACKEND", "groq")
FOUNDER_URL = os.environ.get("FOUNDER_URL", "http://100.73.12.59:4100")

# Groq settings
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_BASE = "https://api.groq.com/openai/v1"

# Ollama settings (used when backend=ollama, e.g. on Oracle A1)
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")

USER_TIMEZONE = os.environ.get("USER_TIMEZONE", "America/Los_Angeles")

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
    # Do NOT set tool_choice — llama-3.3-70b-versatile (Hermes) generates XML
    # function call syntax when tool_choice is explicitly set, causing Groq 400.
    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "tools": TOOLS_SPEC,
        "parallel_tool_calls": False,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{GROQ_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json=payload,
        )
        if not r.is_success:
            err = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            code = (err.get("error") or {}).get("code", "")
            # Tool use format mismatch — retry without tools so user gets a response
            if code == "tool_use_failed" or r.status_code == 400:
                r2 = await client.post(
                    f"{GROQ_BASE}/chat/completions",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                    json={"model": GROQ_MODEL, "messages": messages},
                )
                if r2.is_success:
                    data2 = r2.json()
                    token_ledger[GROQ_MODEL] = token_ledger.get(GROQ_MODEL, 0) + data2.get("usage", {}).get("total_tokens", 0)
                    return {"content": data2["choices"][0]["message"].get("content") or "", "tool_calls": []}
            raise RuntimeError(f"Groq {r.status_code}: {r.text[:500]}")
        data = r.json()
        token_ledger[GROQ_MODEL] = token_ledger.get(GROQ_MODEL, 0) + data.get("usage", {}).get("total_tokens", 0)
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


# ─── log ──────────────────────────────────────────────────────────────────────

def update_log(status: str, tool_calls: list[str] | None = None) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    backend_label = f"Groq/{GROQ_MODEL}" if OVERSEER_BACKEND == "groq" else f"Ollama/{OLLAMA_MODEL}"
    tool_lines = "\n".join(f"- `{t}`" for t in (tool_calls or [])[-10:]) or "*no calls*"
    content = f"---\ntags: [overseer, log]\n---\n\n# Overseer Log\n\n- **{now}** — {backend_label}\n- {status}\n\n## Last calls\n\n{tool_lines}\n"
    log_path = VAULT_PATH / "memory" / "overseer-live.md"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(content)
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

    skills_index = ""
    skills_dir = VAULT_PATH / "overseer" / "skills"
    if skills_dir.exists():
        for d in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
            sf = d / "SKILL.md"
            if sf.exists():
                first_line = sf.read_text().splitlines()[0].lstrip("#").strip()
                skills_index += f"\n- {d.name}: {first_line}"

    return f"""You are Overseer. You route raw data into the vault and retrieve it on demand. You have tools to read, write, and search the vault.

RULES:
- Casual greetings, check-ins, or questions about yourself ("are you working?", "what can you do?"): respond briefly and naturally in 1-2 sentences. Do not search the vault.
- Storing data: do it silently, confirm in one line: what was stored and where.
- Answering factual questions about stored data: search the vault first. Return exactly what you find. If nothing found: "[not in vault: <query>]".
- Never invent or infer facts. Only state what is in the vault.
- Responses are one or two sentences unless more is explicitly requested.
- If an event is missing a date, ask exactly one question to get it.
- No filler phrases. No "I've noted that". No "Certainly!".

NO DRIFT — every write must keep the vault consistent:
- Person fact: write to memory/facts/people.md AND wiki/personal/people/NAME.md
- System change: write to memory/facts/ AND wiki/systems/NAME.md
- Before writing any wiki page: read it first and merge — never overwrite, only append or update
- Tags: lowercase kebab-case. Dates: YYYY-MM-DD always included.

ROUTING — when raw data arrives, route it:
- Person, relationship, contact: wiki/personal/people/FIRSTNAME.md
- Birthday or fact about a person: memory/facts/people.md and the person's note
- Personal preference: memory/facts/preferences.md
- Recurring schedule: memory/facts/recurring.md
- Past event: wiki/sessions/events/YYYY-MM-DD-SLUG.md
- Project info: wiki/projects/PROJECT-NAME.md
- System or infra change: wiki/systems/NAME.md
- Work session debrief: wiki/sessions/YYYY-MM-DD-SLUG.md
- Anything else raw: inbox/yap/YYYY-MM-DD-SLUG.md

SKILLS — before performing a complex task, read the relevant skill file first:
{skills_index or "(no skills loaded)"}

Current facts:
{facts}

Today: {datetime.now(ZoneInfo(USER_TIMEZONE)).strftime("%Y-%m-%d, %A, %H:%M %Z")}"""


# ─── entity extraction ────────────────────────────────────────────────────────

async def extract_entities(text: str) -> dict:
    tz = ZoneInfo(USER_TIMEZONE)
    local_now = datetime.now(tz)
    local_dt = local_now.strftime("%Y-%m-%dT%H:%M:%S%z")
    today = local_now.strftime("%Y-%m-%d")

    prompt = f"""Extract trackable entities from this text. User is Sam, in Seattle (America/Los_Angeles).
Current local datetime: {local_dt}

Return ONLY valid JSON, no other text:
{{
  "people": [{{"name": "string", "relation": "string", "facts": ["string"]}}],
  "events": [{{"description": "string", "date_hint": "string", "approximate": true}}],
  "todos": [{{"task": "string", "person": "string or null", "urgency": "normal"}}],
  "locations": [{{"name": "string", "context": "string"}}],
  "facts": [{{"category": "preference|recurring|personal", "content": "string"}}]
}}

Rules:
- Only include what is explicitly stated. Do not infer.
- Interpret relative dates relative to {local_dt}.
- Mark approximate dates with "approximate": true.
- If nothing to extract for a category, use empty array.

Text: {text}"""

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{GROQ_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}]},
            )
            if not r.is_success:
                return {"error": f"groq {r.status_code}", "vault_writes": []}
            raw = r.json()["choices"][0]["message"]["content"]
        start = raw.find("{")
        end = raw.rfind("}") + 1
        entities = json.loads(raw[start:end])
    except Exception as e:
        return {"error": str(e), "vault_writes": []}

    vault_writes: list[str] = []

    # People → wiki/personal/people/NAME.md + memory/facts/people.md
    for person in entities.get("people", []):
        name = (person.get("name") or "").strip()
        if not name:
            continue
        slug = name.lower().replace(" ", "-")
        person_path = f"wiki/personal/people/{slug}.md"
        existing = vault_read(person_path)
        facts_lines = "\n".join(f"- {f}" for f in person.get("facts", []))

        if "[not found:" in existing:
            relation = person.get("relation", "")
            content = (
                f"---\ntitle: {name}\npartition: personal\ntype: person\nname: {name}\n"
                f"relationship: {relation}\nbirthday: \nlast_contact: {today}\n"
                f"tags: [people, personal]\nsources: [overseer]\ncreated: {today}\nupdated: {today}\n---\n\n"
                f"# {name}\n\nPart of [[personal/MOC|Personal]].\n\n## Facts\n\n{facts_lines}\n\n## Notes\n\n## Interactions\n"
            )
        else:
            if facts_lines and "## Facts" in existing:
                content = existing.rstrip() + f"\n{facts_lines}\n"
            else:
                content = existing

        vault_write(person_path, content, f"overseer: update person {name}")
        vault_writes.append(person_path)

        if person.get("facts"):
            facts_file = "memory/facts/people.md"
            existing_facts = vault_read(facts_file)
            new_lines = "\n".join(f"- **{name}** — {f} (added {today})" for f in person["facts"])
            if "[not found:" not in existing_facts:
                updated = existing_facts.rstrip() + f"\n{new_lines}\n"
            else:
                updated = f"---\ntags: [memory, facts, people]\nupdated: {today}\n---\n\n# People Facts\n\n{new_lines}\n"
            vault_write(facts_file, updated, f"overseer: facts for {name}")
            vault_writes.append(facts_file)

    # Todos → inbox/yap/DATE-todos.md
    todos = entities.get("todos", [])
    if todos:
        todo_path = f"inbox/yap/{today}-todos.md"
        existing_todos = vault_read(todo_path)
        lines = "\n".join(
            f"- [ ] {t['task']}" + (f" (re: {t['person']})" if t.get("person") else "")
            for t in todos
        )
        if "[not found:" not in existing_todos:
            updated_todos = existing_todos.rstrip() + f"\n{lines}\n"
        else:
            updated_todos = f"---\ndate: {today}\ntags: [inbox, todos]\n---\n\n# Todos {today}\n\n{lines}\n"
        vault_write(todo_path, updated_todos, f"overseer: todos {today}")
        vault_writes.append(todo_path)

    # Events → wiki/sessions/events/DATE-slug.md
    for event in entities.get("events", []):
        desc = (event.get("description") or "").strip()
        if not desc:
            continue
        date_hint = event.get("date_hint") or today
        approx = event.get("approximate", False)
        slug_desc = desc.lower()[:30].replace(" ", "-").replace(",", "").replace(".", "")
        event_path = f"wiki/sessions/events/{today}-{slug_desc}.md"
        approx_note = " (approximate)" if approx else ""
        content = (
            f"---\ndate: {date_hint}{approx_note}\ntags: [events]\ncreated: {today}\n---\n\n"
            f"# {desc}\n\nDate: {date_hint}{approx_note}\n"
        )
        vault_write(event_path, content, f"overseer: event {slug_desc}")
        vault_writes.append(event_path)

    # Preferences + personal facts → memory/facts/preferences.md
    pref_facts = [f for f in entities.get("facts", []) if f.get("category") in ("preference", "personal")]
    if pref_facts:
        pref_path = "memory/facts/preferences.md"
        existing_prefs = vault_read(pref_path)
        lines = "\n".join(f"- {f['content']} (added {today})" for f in pref_facts)
        if "[not found:" not in existing_prefs:
            updated_prefs = existing_prefs.rstrip() + f"\n{lines}\n"
        else:
            updated_prefs = f"---\ntags: [memory, facts, preferences]\nupdated: {today}\n---\n\n# Preferences\n\n{lines}\n"
        vault_write(pref_path, updated_prefs, "overseer: update preferences")
        vault_writes.append(pref_path)

    # Recurring facts → memory/facts/recurring.md
    rec_facts = [f for f in entities.get("facts", []) if f.get("category") == "recurring"]
    if rec_facts:
        rec_path = "memory/facts/recurring.md"
        existing_rec = vault_read(rec_path)
        lines = "\n".join(f"- {f['content']} (added {today})" for f in rec_facts)
        if "[not found:" not in existing_rec:
            updated_rec = existing_rec.rstrip() + f"\n{lines}\n"
        else:
            updated_rec = f"---\ntags: [memory, facts, recurring]\nupdated: {today}\n---\n\n# Recurring\n\n{lines}\n"
        vault_write(rec_path, updated_rec, "overseer: update recurring")
        vault_writes.append(rec_path)

    return {"entities": entities, "vault_writes": vault_writes}


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


class ExtractRequest(BaseModel):
    text: str
    session_id: str | None = None


# ─── endpoints ────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse(Path(__file__).parent / "index.html")


@app.get("/dashboard")
async def dashboard_ui():
    return FileResponse(Path(__file__).parent / "dashboard.html")


@app.get("/status")
async def status():
    """Aggregate founder-helper data server-side to avoid browser CORS."""
    async def fetch(path: str, timeout: float = 5.0) -> dict:
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(f"{FOUNDER_URL}{path}")
                if r.is_success:
                    return r.json()
                return {"error": f"http {r.status_code}"}
        except Exception as e:
            return {"error": type(e).__name__, "detail": str(e)[:120]}

    # Tailscale is slow (API call); use longer timeout but cap it
    runtime, tailscale = await asyncio.gather(
        fetch("/runtime/summary", timeout=5.0),
        fetch("/tailscale/devices", timeout=8.0),
    )
    return {"runtime": runtime, "tailscale": tailscale}


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
        update_log(f"Processing: {req.message[:80]}...")
        messages = [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": req.message},
        ]
        answer, tool_log = await run_tool_loop(messages)
        flush_token_ledger()
        update_log(f"Done: {req.message[:60]}", tool_log)
        return {"response": answer, "tool_calls": tool_log, "backend": OVERSEER_BACKEND, "extracted": None}
    except Exception as e:
        import traceback
        return {"response": None, "error": str(e), "trace": traceback.format_exc()[-1000:], "backend": OVERSEER_BACKEND}


@app.post("/extract")
async def extract(req: ExtractRequest):
    result = await extract_entities(req.text)
    return result


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


@app.get("/logs")
async def logs():
    async def gen():
        log_path = VAULT_PATH / "memory" / "overseer-live.md"
        last_mtime = 0.0
        for _ in range(120):
            try:
                mtime = log_path.stat().st_mtime
                if mtime != last_mtime:
                    last_mtime = mtime
                    content = log_path.read_text()
                    yield f"data: {json.dumps({'content': content})}\n\n"
            except Exception:
                pass
            await asyncio.sleep(2)
    return StreamingResponse(gen(), media_type="text/event-stream")
