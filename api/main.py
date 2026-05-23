"""
Overseer API — local inference node.
Runs on this machine. Talks to Ollama. Writes to ~/vault.
Registers with the always-on VPS gateway and serves /infer/chat for routing.
"""
import asyncio
import json
import logging
import os
import re
import socket
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

VAULT_PATH = Path(os.environ.get("VAULT_PATH", str(Path.home() / "vault")))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "dolphin3:latest")
USER_TIMEZONE = os.environ.get("USER_TIMEZONE", "America/Los_Angeles")
SLEIPNIR_DB = Path(os.environ.get("SLEIPNIR_DB", str(Path.home() / ".local/share/urchin/sleipnir.db")))

# Gateway registration (optional — only if GATEWAY_URL is set)
GATEWAY_URL = os.environ.get("GATEWAY_URL", "").rstrip("/")
GATEWAY_NODE_SECRET = os.environ.get("NODE_SECRET", "")
NODE_ID = os.environ.get("NODE_ID", f"saucemachine-{socket.gethostname()}")
NODE_INFERENCE_URL = os.environ.get("NODE_INFERENCE_URL", "")  # public or Tailscale URL of this node
NODE_PORT = int(os.environ.get("NODE_PORT", "7860"))

STATE_DIR = Path(os.environ.get("OVERSEER_STATE_DIR", str(Path.home() / ".local/state/overseer")))
THREADS_DIR = STATE_DIR / "threads"

_SKIP_EXTRACTION_RE = re.compile(
    r"^\s*(hi|hello|hey|ok|k|thanks|thx|sure|yes|no|yep|nope|cool|got it|lol|haha|bye|done|nice)\W*$",
    re.IGNORECASE,
)

# Thread history: thread_id → list of {role, content} messages
_threads: dict[str, list[dict]] = {}
_thread_lock = threading.Lock()

app = FastAPI(title="Overseer", version="2.0.0")


# ─── vault tools ──────────────────────────────────────────────────────────────

def vault_read(path: str) -> str:
    full = VAULT_PATH / path.lstrip("/")
    if not full.exists():
        return f"[not found: {path}]"
    if full.is_dir():
        entries = sorted(p.name for p in full.iterdir())
        if not entries:
            return f"[directory empty: {path}]"
        return "\n".join(f"{path.rstrip('/')}/{name}" for name in entries[:100])
    return full.read_text()


def vault_write(path: str, content: str, commit_msg: str | None = None) -> str:
    full = VAULT_PATH / path.lstrip("/")
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    msg = commit_msg or f"overseer: update {path}"
    try:
        git = ["git", "-C", str(VAULT_PATH)]
        subprocess.run(git + ["config", "user.email", "overseer@local"], capture_output=True)
        subprocess.run(git + ["config", "user.name", "Overseer"], capture_output=True)
        subprocess.run(git + ["add", str(full)], check=True, capture_output=True)
        subprocess.run(git + ["commit", "-m", msg], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        pass  # vault may not be a git repo — write succeeded regardless
    return f"wrote {path}"


def vault_search(query: str, max_results: int = 10) -> str:
    result = subprocess.run(
        ["rg", "--ignore-case", "--max-count=3", "--with-filename", query, str(VAULT_PATH)],
        capture_output=True, text=True,
    )
    lines = result.stdout.strip().splitlines()[:max_results]
    return "\n".join(lines) if lines else "[no results]"


def list_notes(folder: str) -> str:
    full = VAULT_PATH / folder.lstrip("/")
    if not full.is_dir():
        return f"[not a directory: {folder}]"
    files = [str(p.relative_to(VAULT_PATH)) for p in sorted(full.rglob("*.md"))]
    return "\n".join(files) if files else "[empty]"


def sleipnir_query(hours: int = 24, source: str | None = None) -> str:
    if not SLEIPNIR_DB.exists():
        return "[sleipnir.db not found — Sleipnir may not be running yet]"
    try:
        conn = sqlite3.connect(str(SLEIPNIR_DB))
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        if source:
            rows = conn.execute(
                "SELECT source, started_at, ended_at, event_count FROM clusters "
                "WHERE started_at > ? AND source = ? ORDER BY started_at DESC LIMIT 50",
                (cutoff, source),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT source, started_at, ended_at, event_count FROM clusters "
                "WHERE started_at > ? ORDER BY started_at DESC LIMIT 50",
                (cutoff,),
            ).fetchall()
        conn.close()
    except Exception as e:
        return f"[sleipnir error: {e}]"
    if not rows:
        return f"[no activity in last {hours}h]"
    lines = [f"- {r[0]}: {r[1][:16]} → {r[2][:16]} ({r[3]} events)" for r in rows]
    return "\n".join(lines)


def write_calendar_event(
    date: str,
    title: str,
    time: str = "",
    duration_mins: int = 60,
    location: str = "",
    attendees: list[str] | None = None,
    recurring: bool = False,
    notes: str = "",
) -> str:
    slug = title.lower()[:40].replace(" ", "-").replace(",", "").replace(".", "")
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    path = f"wiki/calendar/events/{date}-{slug}.md"
    attendees_str = ", ".join(attendees or [])
    content = (
        f"---\ntitle: {title}\npartition: calendar\ntype: calendar-event\n"
        f"date: {date}\ntime: {time}\nduration_mins: {duration_mins}\n"
        f"location: {location}\nattendees: [{attendees_str}]\nrecurring: {str(recurring).lower()}\n"
        f"sources: [overseer]\ncreated: {date}\nupdated: {date}\ntags: [calendar]\n---\n\n"
        f"# {title}\n\n{notes}\n"
    )
    return vault_write(path, content, f"overseer: calendar event — {title}")


def write_health_daily(
    date: str,
    steps: int = 0,
    sleep_hours: float = 0.0,
    weight_kg: float = 0.0,
    hrv: int = 0,
    energy: str = "",
    notes: str = "",
) -> str:
    path = f"wiki/health/daily/{date}.md"
    existing = vault_read(path)
    if "[not found:" not in existing:
        # merge: update only non-zero fields
        content = existing
        if steps:
            content = re.sub(r"steps: \d+", f"steps: {steps}", content)
        if sleep_hours:
            content = re.sub(r"sleep_hours: [\d.]+", f"sleep_hours: {sleep_hours}", content)
        if weight_kg:
            content = re.sub(r"weight_kg: [\d.]+", f"weight_kg: {weight_kg}", content)
        if notes:
            content = content.rstrip() + f"\n\n{notes}\n"
    else:
        content = (
            f"---\ntitle: Health {date}\npartition: health\ntype: health-daily\n"
            f"date: {date}\nsteps: {steps}\nsleep_hours: {sleep_hours}\n"
            f"weight_kg: {weight_kg}\nhrv: {hrv}\nenergy: {energy}\n"
            f"sources: [overseer]\ncreated: {date}\nupdated: {date}\ntags: [health, daily]\n---\n\n"
            f"# Health {date}\n\n{notes}\n"
        )
    return vault_write(path, content, f"overseer: health {date}")


def write_place_visit(
    date: str,
    name: str,
    city: str = "",
    lat: float = 0.0,
    lng: float = 0.0,
    duration_mins: int = 0,
    notes: str = "",
) -> str:
    slug = name.lower()[:30].replace(" ", "-")
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    path = f"wiki/places/visits/{date}-{slug}.md"
    content = (
        f"---\ntitle: {name}\npartition: places\ntype: place-visit\n"
        f"date: {date}\nlat: {lat}\nlng: {lng}\nname: {name}\ncity: {city}\n"
        f"duration_mins: {duration_mins}\nsources: [overseer]\ncreated: {date}\n"
        f"updated: {date}\ntags: [places]\n---\n\n"
        f"# {name}\n\n{notes}\n"
    )
    return vault_write(path, content, f"overseer: place — {name}")


def write_finance_transaction(
    date: str,
    amount: float,
    merchant: str,
    category: str = "other",
    currency: str = "USD",
    account: str = "checking",
    notes: str = "",
) -> str:
    slug = merchant.lower()[:25].replace(" ", "-")
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    path = f"wiki/finance/transactions/{date}-{slug}.md"
    content = (
        f"---\ntitle: {merchant} {date}\npartition: finance\ntype: transaction\n"
        f"date: {date}\namount: {amount:.2f}\ncurrency: {currency}\n"
        f"merchant: {merchant}\ncategory: {category}\naccount: {account}\n"
        f"sources: [overseer]\ncreated: {date}\nupdated: {date}\ntags: [finance, {category}]\n---\n\n"
        f"# {merchant}\n\n{notes}\n"
    )
    return vault_write(path, content, f"overseer: transaction — {merchant}")


def write_todo(task: str, due_date: str = "", person: str = "", priority: str = "normal") -> str:
    tz = ZoneInfo(USER_TIMEZONE)
    today = datetime.now(tz).strftime("%Y-%m-%d")
    todo_path = f"inbox/yap/{today}-todos.md"
    existing = vault_read(todo_path)
    due_str = f" 📅 {due_date}" if due_date else ""
    person_str = f" (re: {person})" if person else ""
    entry = f"- [ ] {task}{person_str}{due_str}"
    if "[not found:" not in existing:
        content = existing.rstrip() + f"\n{entry}\n"
    else:
        content = (
            f"---\ndate: {today}\npartition: personal\ntype: todos\n"
            f"sources: [overseer]\ntags: [inbox, todos]\n---\n\n"
            f"# Todos {today}\n\n{entry}\n"
        )
    return vault_write(todo_path, content, f"overseer: todo — {task[:40]}")


def update_wiki_page(path: str, section: str, content_to_append: str) -> str:
    existing = vault_read(path)
    if "[not found:" in existing:
        return f"[page not found: {path}]"
    if section and f"## {section}" in existing:
        insert_at = existing.index(f"## {section}") + len(f"## {section}")
        next_section = existing.find("\n## ", insert_at)
        if next_section == -1:
            updated = existing.rstrip() + f"\n{content_to_append}\n"
        else:
            updated = existing[:next_section] + f"\n{content_to_append}" + existing[next_section:]
    else:
        updated = existing.rstrip() + f"\n\n{content_to_append}\n"
    return vault_write(path, updated, f"overseer: update {path}")


TOOLS_MAP = {
    "vault_read": vault_read,
    "vault_write": vault_write,
    "vault_search": vault_search,
    "list_notes": list_notes,
    "sleipnir_query": sleipnir_query,
    "write_calendar_event": write_calendar_event,
    "write_health_daily": write_health_daily,
    "write_place_visit": write_place_visit,
    "write_finance_transaction": write_finance_transaction,
    "write_todo": write_todo,
    "update_wiki_page": update_wiki_page,
}

TOOLS_SPEC = [
    {"type": "function", "function": {
        "name": "vault_read",
        "description": "Read a file or directory from the vault. Path relative to vault root (e.g. 'wiki/personal/people/john.md').",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "vault_write",
        "description": "Write content to a vault file. Creates parent directories. Commits locally.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}, "commit_msg": {"type": "string"}},
            "required": ["path", "content"],
        },
    }},
    {"type": "function", "function": {
        "name": "vault_search",
        "description": "Search vault using ripgrep. Returns matching lines with file paths.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "list_notes",
        "description": "List all markdown files in a vault folder.",
        "parameters": {"type": "object", "properties": {"folder": {"type": "string"}}, "required": ["folder"]},
    }},
    {"type": "function", "function": {
        "name": "sleipnir_query",
        "description": "Query recent activity clusters from Sleipnir (the distillation layer). Returns what Sam was working on.",
        "parameters": {
            "type": "object",
            "properties": {
                "hours": {"type": "integer", "description": "How many hours back to look (default 24)"},
                "source": {"type": "string", "description": "Filter by source name (e.g. 'git', 'shell', 'browser')"},
            },
        },
    }},
    {"type": "function", "function": {
        "name": "write_calendar_event",
        "description": "Write a calendar event to wiki/calendar/events/ with correct frontmatter.",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "title": {"type": "string"},
                "time": {"type": "string", "description": "HH:MM (24h)"},
                "duration_mins": {"type": "integer"},
                "location": {"type": "string"},
                "attendees": {"type": "array", "items": {"type": "string"}},
                "recurring": {"type": "boolean"},
                "notes": {"type": "string"},
            },
            "required": ["date", "title"],
        },
    }},
    {"type": "function", "function": {
        "name": "write_health_daily",
        "description": "Write or update a daily health note in wiki/health/daily/.",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "steps": {"type": "integer"},
                "sleep_hours": {"type": "number"},
                "weight_kg": {"type": "number"},
                "hrv": {"type": "integer"},
                "energy": {"type": "string", "description": "low/medium/high or 1-10"},
                "notes": {"type": "string"},
            },
            "required": ["date"],
        },
    }},
    {"type": "function", "function": {
        "name": "write_place_visit",
        "description": "Record a place visit in wiki/places/visits/. Appears as a pin in Map View.",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "name": {"type": "string"},
                "city": {"type": "string"},
                "lat": {"type": "number"},
                "lng": {"type": "number"},
                "duration_mins": {"type": "integer"},
                "notes": {"type": "string"},
            },
            "required": ["date", "name"],
        },
    }},
    {"type": "function", "function": {
        "name": "write_finance_transaction",
        "description": "Write a financial transaction to wiki/finance/transactions/.",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "amount": {"type": "number"},
                "merchant": {"type": "string"},
                "category": {"type": "string", "description": "food|transport|software|client|other"},
                "currency": {"type": "string"},
                "account": {"type": "string", "description": "checking|savings|card"},
                "notes": {"type": "string"},
            },
            "required": ["date", "amount", "merchant"],
        },
    }},
    {"type": "function", "function": {
        "name": "write_todo",
        "description": "Add a todo item to today's inbox todo list.",
        "parameters": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                "person": {"type": "string"},
                "priority": {"type": "string", "description": "low|normal|high"},
            },
            "required": ["task"],
        },
    }},
    {"type": "function", "function": {
        "name": "update_wiki_page",
        "description": "Append content to a specific section of an existing wiki page.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "section": {"type": "string", "description": "Section heading (without ##)"},
                "content_to_append": {"type": "string"},
            },
            "required": ["path", "content_to_append"],
        },
    }},
]


# ─── LLM (Ollama only) ────────────────────────────────────────────────────────

async def ollama_chat(messages: list[dict]) -> dict:
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "tools": TOOLS_SPEC,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()
    msg = data.get("message", {})
    return {
        "content": msg.get("content") or "",
        "tool_calls": msg.get("tool_calls") or [],
    }


async def run_tool_loop(messages: list[dict]) -> tuple[str, list[str]]:
    tool_log: list[str] = []
    for _ in range(12):
        response = await ollama_chat(messages)
        content = response["content"]
        tool_calls = response["tool_calls"]

        if not tool_calls:
            return content, tool_log

        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            raw_args = fn.get("arguments", {})
            args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})

            short_args = ", ".join(f"{k}={repr(v)[:40]}" for k, v in args.items())
            tool_log.append(f"{name}({short_args})")

            tool_fn = TOOLS_MAP.get(name)
            if tool_fn:
                try:
                    result = tool_fn(**args)
                except Exception as e:
                    result = f"[tool error: {e}]"
            else:
                result = f"[unknown tool: {name}]"

            tool_msg: dict = {"role": "tool", "content": str(result)[:8000]}
            if "id" in tc:
                tool_msg["tool_call_id"] = tc["id"]
            messages.append(tool_msg)

    return "Max tool iterations reached.", tool_log


# ─── system prompt ────────────────────────────────────────────────────────────

def _recent_activity_context() -> str:
    activity = sleipnir_query(hours=3)
    if "[" in activity and "not found" in activity:
        return ""
    if "[no activity" in activity:
        return ""
    return f"\nRecent activity (last 3h):\n{activity}\n"


def _load_facts() -> str:
    FACTS_CAP = 2000
    buf = ""
    facts_dir = VAULT_PATH / "memory" / "facts"
    if not facts_dir.exists():
        return "[none yet]"
    for f in sorted(facts_dir.glob("*.md")):
        try:
            chunk = f"\n### {f.stem}\n{f.read_text()[:600]}"
            if len(buf) + len(chunk) > FACTS_CAP:
                buf += "\n[facts truncated]"
                break
            buf += chunk
        except Exception:
            pass
    return buf or "[none yet]"


def _skills_index() -> str:
    skills_dir = VAULT_PATH / "overseer" / "skills"
    if not skills_dir.exists():
        return ""
    lines = []
    for d in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        sf = d / "SKILL.md"
        if sf.exists():
            first_line = sf.read_text().splitlines()[0].lstrip("#").strip()
            lines.append(f"- {d.name}: {first_line}")
    return "\n".join(lines)


_system_prompt_cache: tuple[float, str] | None = None
_SYSTEM_PROMPT_TTL = 120  # rebuild every 2 minutes to pick up new Sleipnir data


def build_system_prompt() -> str:
    tz = ZoneInfo(USER_TIMEZONE)
    now_str = datetime.now(tz).strftime("%Y-%m-%d, %A, %H:%M %Z")
    today = datetime.now(tz).strftime("%Y-%m-%d")
    activity = _recent_activity_context()
    facts = _load_facts()
    skills = _skills_index()

    return f"""You are Overseer — Sam's sovereign local brain. You run offline. You know his vault schema.

IDENTITY:
- Sam Charles, 21, Capitol Hill Seattle. Founder: Orinadus + Mad House.
- You are always next to him in a terminal. You watch. You remember. You act.

RULES:
- Never invent or infer facts. Only state what is in the vault.
- If you don't know something, say so and offer to search.
- Responses are 1-3 sentences unless Sam asks for more.
- No filler. No "Certainly!". No "I've noted that".
- If a date is missing and it matters, ask exactly one question.
- Casual check-ins: respond briefly without searching the vault.

WHAT YOU DO:
- Sam talks. You listen. You route information to the right vault partition.
- Calendar events → write_calendar_event (appears in Obsidian Full Calendar)
- Health data → write_health_daily (appears in Obsidian Health dashboard)
- Places visited → write_place_visit (appears as pins in Map View)
- Financial transactions → write_finance_transaction (appears in Finance dashboard)
- Todos and tasks → write_todo (uses Obsidian Tasks syntax with due dates)
- People and relationships → wiki/personal/people/NAME.md + memory/facts/people.md
- Movies, books, articles → wiki/personal/{{movies|books|articles}}.md (append row)
- Knowledge / links → wiki/knowledge/{{slug}}.md (create stub, tag [EMERGING])
- Project updates → wiki/madhouse/ or wiki/orinadus/ (read first, then update)
- Anything ambiguous → read wiki/_index.md to find the right page

NO DRIFT — before every vault_write:
1. vault_read the target page and merge — never blindly overwrite
2. Correct frontmatter partition and tags per VAULT.md schema
3. Append to wiki/_log.md: [YYYY-MM-DD] conversation | <summary> | <pages touched>

VAULT PARTITIONS:
- orinadus: Urchin, Sleipnir, platform architecture
- madhouse: Chopsticks, agents, projects, brand
- personal: goals, relationships, identity, media, tracking
- finance: transactions, budgets, accounts
- health: daily metrics, sleep, fitness
- calendar: events, appointments
- places: location history, visits
- knowledge: research, learnings
- systems: infrastructure, tooling

SKILLS (read skill file before complex tasks):{f'''
{skills}''' if skills else ' (none loaded)'}

CURRENT FACTS:
{facts}
{activity}
Today: {now_str}"""


def system_prompt() -> str:
    global _system_prompt_cache
    now = time.monotonic()
    if _system_prompt_cache and now - _system_prompt_cache[0] < _SYSTEM_PROMPT_TTL:
        return _system_prompt_cache[1]
    prompt = build_system_prompt()
    _system_prompt_cache = (now, prompt)
    return prompt


# ─── thread persistence ───────────────────────────────────────────────────────

def _thread_path(thread_id: str) -> Path:
    THREADS_DIR.mkdir(parents=True, exist_ok=True)
    return THREADS_DIR / f"{thread_id}.json"


def load_thread(thread_id: str) -> list[dict]:
    with _thread_lock:
        if thread_id in _threads:
            return list(_threads[thread_id])
    path = _thread_path(thread_id)
    if path.exists():
        try:
            history = json.loads(path.read_text())
            with _thread_lock:
                _threads[thread_id] = history
            return list(history)
        except Exception:
            pass
    return []


def save_thread(thread_id: str, history: list[dict]) -> None:
    with _thread_lock:
        _threads[thread_id] = history
    try:
        path = _thread_path(thread_id)
        path.write_text(json.dumps(history[-40:], indent=2))  # keep last 40 turns
    except Exception:
        pass


# ─── entity extraction (smart routing) ───────────────────────────────────────

async def extract_entities(text: str) -> dict:
    if len(text.split()) < 5 or _SKIP_EXTRACTION_RE.match(text):
        return {"entities": {}, "vault_writes": [], "skipped": True}

    tz = ZoneInfo(USER_TIMEZONE)
    local_now = datetime.now(tz)
    today = local_now.strftime("%Y-%m-%d")
    local_dt = local_now.strftime("%Y-%m-%dT%H:%M:%S%z")

    prompt = f"""Extract trackable entities from Sam's message. Sam is in Seattle (America/Los_Angeles).
Current local datetime: {local_dt}

Return ONLY valid JSON:
{{
  "people": [{{"name": "string", "relation": "string", "facts": ["string"]}}],
  "calendar_events": [{{"title": "string", "date": "YYYY-MM-DD", "time": "HH:MM", "duration_mins": 60, "location": "", "attendees": [], "notes": ""}}],
  "todos": [{{"task": "string", "due_date": "YYYY-MM-DD or empty", "person": "string or null", "priority": "normal"}}],
  "locations": [{{"name": "string", "city": "string", "duration_mins": 0, "notes": ""}}],
  "health": [{{"steps": 0, "sleep_hours": 0.0, "weight_kg": 0.0, "energy": "", "notes": ""}}],
  "transactions": [{{"amount": 0.0, "merchant": "string", "category": "other", "account": "checking", "notes": ""}}],
  "facts": [{{"category": "preference|recurring|personal", "content": "string"}}],
  "books": [{{"title": "string", "author": "string or null", "context": "string"}}],
  "movies": [{{"title": "string", "year": "string or null", "context": "string"}}],
  "articles": [{{"title": "string", "url": "string or null"}}],
  "knowledge_domains": [{{"domain": "string", "context": "string"}}]
}}

Rules:
- Only extract what is explicitly stated. Never infer.
- Interpret relative dates relative to {local_dt}.
- Empty arrays for categories with nothing to extract.

Text: {text}"""

    messages = [{"role": "user", "content": prompt}]
    try:
        response = await ollama_chat(messages)
        raw = response["content"]
        start = raw.find("{")
        end = raw.rfind("}") + 1
        entities = json.loads(raw[start:end])
    except Exception as e:
        return {"error": str(e), "vault_writes": []}

    vault_writes: list[str] = []

    # Calendar events
    for event in entities.get("calendar_events", []):
        if not event.get("title"):
            continue
        result = write_calendar_event(
            date=event.get("date") or today,
            title=event["title"],
            time=event.get("time") or "",
            duration_mins=event.get("duration_mins") or 60,
            location=event.get("location") or "",
            attendees=event.get("attendees") or [],
            notes=event.get("notes") or "",
        )
        vault_writes.append(result)

    # Health
    for h in entities.get("health", []):
        if any(h.get(k) for k in ("steps", "sleep_hours", "weight_kg", "energy")):
            result = write_health_daily(
                date=today,
                steps=h.get("steps") or 0,
                sleep_hours=h.get("sleep_hours") or 0.0,
                weight_kg=h.get("weight_kg") or 0.0,
                energy=h.get("energy") or "",
                notes=h.get("notes") or "",
            )
            vault_writes.append(result)

    # Transactions
    for tx in entities.get("transactions", []):
        if tx.get("merchant") and tx.get("amount"):
            result = write_finance_transaction(
                date=today,
                amount=tx["amount"],
                merchant=tx["merchant"],
                category=tx.get("category") or "other",
                account=tx.get("account") or "checking",
                notes=tx.get("notes") or "",
            )
            vault_writes.append(result)

    # Todos
    for todo in entities.get("todos", []):
        if todo.get("task"):
            result = write_todo(
                task=todo["task"],
                due_date=todo.get("due_date") or "",
                person=todo.get("person") or "",
                priority=todo.get("priority") or "normal",
            )
            vault_writes.append(result)

    # People
    for person in entities.get("people", []):
        name = (person.get("name") or "").strip()
        if not name:
            continue
        slug = name.lower().replace(" ", "-")
        person_path = f"wiki/personal/people/{slug}.md"
        existing = vault_read(person_path)
        facts_lines = "\n".join(f"- {f}" for f in person.get("facts", []))
        if "[not found:" in existing:
            content = (
                f"---\ntitle: {name}\npartition: personal\ntype: person\nname: {name}\n"
                f"relationship: {person.get('relation', '')}\nbirthday: \nlast_contact: {today}\n"
                f"sources: [overseer]\ncreated: {today}\nupdated: {today}\ntags: [people, personal]\n---\n\n"
                f"# {name}\n\n## Facts\n\n{facts_lines}\n\n## Notes\n\n## Interactions\n"
            )
        else:
            content = existing.rstrip() + (f"\n{facts_lines}\n" if facts_lines else "")
        vault_write(person_path, content, f"overseer: person {name}")
        vault_writes.append(person_path)

    # Books
    for book in entities.get("books", []):
        title = (book.get("title") or "").strip()
        if not title:
            continue
        books_path = "wiki/personal/books.md"
        existing = vault_read(books_path)
        author = book.get("author") or "unknown"
        context = book.get("context") or ""
        entry = f"| {title} | {author} | {today} | {context} |"
        if "[not found:" not in existing:
            if title not in existing:
                vault_write(books_path, existing.rstrip() + f"\n{entry}\n", f"overseer: book — {title}")
                vault_writes.append(books_path)
        else:
            header = (
                f"---\ntitle: Books\npartition: personal\nsources: [overseer]\n"
                f"created: {today}\nupdated: {today}\ntags: [personal, books]\n---\n\n"
                f"# Books\n\n| Title | Author | Date | Context |\n|---|---|---|---|\n{entry}\n"
            )
            vault_write(books_path, header, f"overseer: books.md — {title}")
            vault_writes.append(books_path)

    # Movies
    for movie in entities.get("movies", []):
        title = (movie.get("title") or "").strip()
        if not title:
            continue
        movies_path = "wiki/personal/movies.md"
        existing = vault_read(movies_path)
        year = movie.get("year") or ""
        context = movie.get("context") or ""
        entry = f"| {title} | {year} | {today} | {context} |"
        if "[not found:" not in existing:
            if title not in existing:
                vault_write(movies_path, existing.rstrip() + f"\n{entry}\n", f"overseer: movie — {title}")
                vault_writes.append(movies_path)
        else:
            header = (
                f"---\ntitle: Movies\npartition: personal\nsources: [overseer]\n"
                f"created: {today}\nupdated: {today}\ntags: [personal, movies]\n---\n\n"
                f"# Movies\n\n| Title | Year | Date | Context |\n|---|---|---|---|\n{entry}\n"
            )
            vault_write(movies_path, header, f"overseer: movies.md — {title}")
            vault_writes.append(movies_path)

    # Knowledge domains
    for kd in entities.get("knowledge_domains", []):
        domain = (kd.get("domain") or "").strip()
        if not domain:
            continue
        slug = domain.lower().replace(" ", "-").replace("/", "-")
        slug = re.sub(r"[^a-z0-9-]", "", slug)
        kd_path = f"wiki/knowledge/{slug}.md"
        if "[not found:" in vault_read(kd_path) and "[no results]" in vault_search(domain):
            content = (
                f"---\ntitle: {domain}\npartition: knowledge\nsources: [overseer]\n"
                f"created: {today}\nupdated: {today}\ntags: [knowledge, emerging]\n---\n\n"
                f"# {domain}\n\n[EMERGING] — first mentioned {today}.\n\n{kd.get('context', '')}\n"
            )
            vault_write(kd_path, content, f"overseer: emerging domain — {domain}")
            vault_writes.append(kd_path)

    # Preferences / personal facts
    pref_facts = [f for f in entities.get("facts", []) if f.get("category") in ("preference", "personal")]
    if pref_facts:
        pref_path = "memory/facts/preferences.md"
        existing = vault_read(pref_path)
        lines = "\n".join(f"- {f['content']} (added {today})" for f in pref_facts)
        if "[not found:" not in existing:
            vault_write(pref_path, existing.rstrip() + f"\n{lines}\n", "overseer: preferences")
        else:
            vault_write(pref_path, f"---\ntags: [memory, facts, preferences]\nupdated: {today}\n---\n\n# Preferences\n\n{lines}\n", "overseer: preferences")
        vault_writes.append(pref_path)

    # Recurring facts
    rec_facts = [f for f in entities.get("facts", []) if f.get("category") == "recurring"]
    if rec_facts:
        rec_path = "memory/facts/recurring.md"
        existing = vault_read(rec_path)
        lines = "\n".join(f"- {f['content']} (added {today})" for f in rec_facts)
        if "[not found:" not in existing:
            vault_write(rec_path, existing.rstrip() + f"\n{lines}\n", "overseer: recurring")
        else:
            vault_write(rec_path, f"---\ntags: [memory, facts, recurring]\nupdated: {today}\n---\n\n# Recurring\n\n{lines}\n", "overseer: recurring")
        vault_writes.append(rec_path)

    return {"entities": entities, "vault_writes": vault_writes}


def _append_log(summary: str, pages: list[str]) -> None:
    tz = ZoneInfo(USER_TIMEZONE)
    today = datetime.now(tz).strftime("%Y-%m-%d")
    log_path = VAULT_PATH / "wiki" / "_log.md"
    pages_str = ", ".join(pages[:5]) or "none"
    entry = f"\n[{today}] conversation | {summary[:80]} | {pages_str}"
    try:
        if log_path.exists():
            log_path.write_text(log_path.read_text().rstrip() + entry + "\n")
        else:
            log_path.write_text(f"# Vault Operation Log\n{entry}\n")
    except Exception:
        pass


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


# ─── endpoints ────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    index = Path(__file__).parent / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"status": "Overseer running", "vault": str(VAULT_PATH), "model": OLLAMA_MODEL}


@app.get("/health")
async def health():
    ollama_ok = False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            ollama_ok = r.is_success
    except Exception:
        pass

    vault_log = ""
    try:
        r = subprocess.run(
            ["git", "-C", str(VAULT_PATH), "log", "-1", "--format=%ci"],
            capture_output=True, text=True,
        )
        vault_log = r.stdout.strip()
    except Exception:
        pass

    sleipnir_ok = SLEIPNIR_DB.exists()

    return {
        "status": "ok",
        "model": OLLAMA_MODEL,
        "ollama_url": OLLAMA_URL,
        "ollama": "ok" if ollama_ok else "unreachable",
        "vault_path": str(VAULT_PATH),
        "vault_last_commit": vault_log,
        "sleipnir_db": str(SLEIPNIR_DB),
        "sleipnir": "ok" if sleipnir_ok else "not found",
    }


@app.get("/ready")
async def ready():
    return {"status": "ok", "vault": str(VAULT_PATH)}


@app.post("/chat")
async def chat(req: ChatRequest):
    thread_id = req.thread_id or "default"
    history = load_thread(thread_id)

    messages = [{"role": "system", "content": system_prompt()}]
    messages.extend(history[-20:])  # last 20 turns for context
    messages.append({"role": "user", "content": req.message})

    try:
        answer, tool_log = await run_tool_loop(messages)
    except Exception as e:
        import traceback
        return {
            "response": None,
            "error": str(e),
            "trace": traceback.format_exc()[-1000:],
            "tool_calls": [],
        }

    # Persist conversation turn
    history.append({"role": "user", "content": req.message})
    history.append({"role": "assistant", "content": answer})
    save_thread(thread_id, history)

    if tool_log:
        _append_log(req.message[:80], tool_log)

    # Update overseer-live.md
    try:
        live_path = VAULT_PATH / "memory" / "overseer-live.md"
        live_path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        tool_lines = "\n".join(f"- `{t}`" for t in tool_log[-5:]) or "*no tool calls*"
        live_path.write_text(
            f"---\ntags: [overseer, log]\n---\n\n# Overseer Live\n\n"
            f"- **{now}** — {OLLAMA_MODEL}\n- {req.message[:80]}\n\n## Tool calls\n\n{tool_lines}\n"
        )
    except Exception:
        pass

    return {
        "response": answer,
        "tool_calls": tool_log,
        "thread_id": thread_id,
        "model": OLLAMA_MODEL,
    }


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
  "destination": "wiki/orinadus|wiki/madhouse|wiki/personal|wiki/systems|wiki/sessions|wiki/personal/people|daily|ignore",
  "summary": "one sentence",
  "entities": {{"people": [], "dates": [], "projects": [], "facts": []}}
}}

Content:
{req.content[:3000]}"""

    messages = [{"role": "user", "content": triage_prompt}]
    try:
        response = await ollama_chat(messages)
        raw = response["content"]
        start = raw.find("{")
        end = raw.rfind("}") + 1
        return json.loads(raw[start:end])
    except Exception:
        return {
            "tags": ["inbox"],
            "destination": "wiki/personal",
            "summary": req.content[:100],
            "entities": {"people": [], "dates": [], "projects": [], "facts": []},
        }


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


@app.get("/threads/{thread_id}")
async def get_thread(thread_id: str):
    history = load_thread(thread_id)
    return {"thread_id": thread_id, "turns": len(history), "history": history[-20:]}


@app.delete("/threads/{thread_id}")
async def clear_thread(thread_id: str):
    with _thread_lock:
        _threads.pop(thread_id, None)
    path = _thread_path(thread_id)
    if path.exists():
        path.unlink()
    return {"cleared": thread_id}


@app.get("/activity")
async def activity(hours: int = 24, source: str | None = None):
    result = sleipnir_query(hours=hours, source=source)
    return {"hours": hours, "source": source, "activity": result}


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


# ─── gateway node protocol ────────────────────────────────────────────────────
# The gateway calls /infer/chat with the user message + conversation history.
# This node builds its own system prompt (with live vault context) and infers.

class InferRequest(BaseModel):
    message: str
    history: list[dict] = []


@app.post("/infer/chat")
async def infer_chat(req: InferRequest, request: Request):
    if NODE_SECRET:
        secret = request.headers.get("X-Node-Secret", "")
        if secret != GATEWAY_NODE_SECRET:
            raise HTTPException(status_code=401, detail="unauthorized")

    messages = [{"role": "system", "content": system_prompt()}]
    messages.extend(req.history[-20:])
    messages.append({"role": "user", "content": req.message})

    try:
        answer, tool_log = await run_tool_loop(messages)
    except Exception as e:
        import traceback
        return {"content": f"[node error: {e}]", "tool_calls": [], "model": OLLAMA_MODEL}

    if tool_log:
        _append_log(req.message[:80], tool_log)

    return {"content": answer, "tool_calls": tool_log, "model": OLLAMA_MODEL}


# ─── gateway registration ─────────────────────────────────────────────────────

async def _register_with_gateway() -> None:
    if not GATEWAY_URL or not NODE_INFERENCE_URL:
        return

    # Discover available Ollama models
    models: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            if r.is_success:
                models = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        pass

    payload = {
        "node_id": NODE_ID,
        "hostname": socket.gethostname(),
        "inference_url": NODE_INFERENCE_URL,
        "secret": GATEWAY_NODE_SECRET,
        "capabilities": ["chat"],
        "models": models,
        "has_vault": True,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{GATEWAY_URL}/nodes/register", json=payload)
            if r.is_success:
                logging.info("Registered with gateway: %s", GATEWAY_URL)
            else:
                logging.warning("Gateway registration failed: %s", r.text[:200])
    except Exception as e:
        logging.warning("Gateway registration error: %s", e)


async def _heartbeat_loop() -> None:
    if not GATEWAY_URL or not NODE_INFERENCE_URL:
        return
    while True:
        await asyncio.sleep(30)
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(
                    f"{GATEWAY_URL}/nodes/heartbeat",
                    json={"node_id": NODE_ID, "secret": GATEWAY_NODE_SECRET},
                )
        except Exception:
            pass


@app.on_event("startup")
async def startup():
    await _register_with_gateway()
    asyncio.create_task(_heartbeat_loop())
