"""
Overseer API — local inference node.
Runs on this machine. Talks to Ollama. Writes to ~/vault.
Registers with the always-on VPS gateway and serves /infer/chat for routing.
"""
import json
import logging
import os
import re
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

def trunc_str(s: str, n: int) -> str:
    s = (s or "").replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


VAULT_PATH = Path(os.environ.get("VAULT_PATH", str(Path.home() / "vault")))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "dolphin3:latest")
USER_TIMEZONE = os.environ.get("USER_TIMEZONE", "America/Los_Angeles")
SLEIPNIR_DB = Path(os.environ.get("SLEIPNIR_DB", str(Path.home() / ".local/share/urchin/sleipnir.db")))
URCHIN_JOURNAL = Path(os.environ.get("URCHIN_JOURNAL", str(Path.home() / ".local/share/urchin/journal/events.jsonl")))

GATEWAY_NODE_SECRET = os.environ.get("NODE_SECRET", "")

STATE_DIR = Path(os.environ.get("OVERSEER_STATE_DIR", str(Path.home() / ".local/state/overseer")))
THREADS_DIR = STATE_DIR / "threads"

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


def vault_inbox_query(hours: int = 24, source: str | None = None) -> str:
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


def _slug(text: str, maxlen: int = 40) -> str:
    s = text.lower()[:maxlen].replace(" ", "-")
    return re.sub(r"[^a-z0-9-]", "", s) or "untitled"


def write_deal(
    client: str,
    status: str = "lead",
    value: float = 0,
    currency: str = "USD",
    close_date: str = "",
    next_action: str = "",
    notes: str = "",
) -> str:
    tz = ZoneInfo(USER_TIMEZONE)
    today = datetime.now(tz).strftime("%Y-%m-%d")
    path = f"wiki/sales/deals/{_slug(client)}.md"
    content = (
        f"---\ntitle: {client}\npartition: sales\ntype: deal\n"
        f"client: {client}\nstatus: {status}\nvalue: {value}\ncurrency: {currency}\n"
        f"opened: {today}\nclose_date: {close_date}\nnext_action: \"{next_action}\"\n"
        f"sources: [overseer]\ncreated: {today}\nupdated: {today}\ntags: [sales, deal]\n---\n\n"
        f"# {client}\n\n{notes}\n"
    )
    return vault_write(path, content, f"overseer: deal — {client}")


def write_lead(name: str, source: str = "", status: str = "cold", notes: str = "") -> str:
    tz = ZoneInfo(USER_TIMEZONE)
    today = datetime.now(tz).strftime("%Y-%m-%d")
    path = f"wiki/sales/leads/{_slug(name)}.md"
    content = (
        f"---\ntitle: {name}\npartition: sales\ntype: lead\n"
        f"source: \"{source}\"\nfirst_contact: {today}\nstatus: {status}\n"
        f"sources: [overseer]\ncreated: {today}\nupdated: {today}\ntags: [sales, lead]\n---\n\n"
        f"# {name}\n\n{notes}\n"
    )
    return vault_write(path, content, f"overseer: lead — {name}")


def write_client(name: str, mrr: float = 0, relationship: str = "", notes: str = "") -> str:
    tz = ZoneInfo(USER_TIMEZONE)
    today = datetime.now(tz).strftime("%Y-%m-%d")
    path = f"wiki/sales/clients/{_slug(name)}.md"
    content = (
        f"---\ntitle: {name}\npartition: sales\ntype: client\n"
        f"since: {today}\nmrr: {mrr}\nrelationship: \"{relationship}\"\n"
        f"sources: [overseer]\ncreated: {today}\nupdated: {today}\ntags: [sales, client]\n---\n\n"
        f"# {name}\n\n{notes}\n"
    )
    return vault_write(path, content, f"overseer: client — {name}")


def write_bookmark(url: str, title: str = "", topic: str = "", source: str = "", summary: str = "", tags: list[str] | None = None) -> str:
    tz = ZoneInfo(USER_TIMEZONE)
    today = datetime.now(tz).strftime("%Y-%m-%d")
    slug = _slug(title or url.replace("https://", "").replace("http://", ""))
    path = f"wiki/bookmarks/{slug}.md"
    tag_str = ", ".join(tags or [])
    content = (
        f"---\ntitle: {title or url}\npartition: bookmarks\ntype: bookmark\n"
        f"url: {url}\ntopic: \"{topic}\"\nsource: \"{source}\"\nsummary: \"{summary}\"\n"
        f"saved: {today}\nsources: [overseer]\ncreated: {today}\nupdated: {today}\n"
        f"tags: [bookmarks, {tag_str}]\n---\n\n"
        f"# {title or url}\n\n{url}\n\n{summary}\n"
    )
    return vault_write(path, content, f"overseer: bookmark — {title or url[:40]}")


def write_reading_item(
    title: str,
    medium: str = "book",
    author: str = "",
    status: str = "queue",
    progress_pct: int = 0,
    notes: str = "",
) -> str:
    tz = ZoneInfo(USER_TIMEZONE)
    today = datetime.now(tz).strftime("%Y-%m-%d")
    subdir = {"book": "books", "article": "articles", "course": "courses", "podcast": "podcasts"}.get(medium, "books")
    path = f"wiki/reading/{subdir}/{_slug(title)}.md"
    content = (
        f"---\ntitle: {title}\npartition: reading\ntype: reading-item\n"
        f"medium: {medium}\nstatus: {status}\nauthor: \"{author}\"\n"
        f"progress_pct: {progress_pct}\nstarted: \nfinished: \n"
        f"sources: [overseer]\ncreated: {today}\nupdated: {today}\ntags: [reading, {medium}]\n---\n\n"
        f"# {title}\n\n{notes}\n"
    )
    return vault_write(path, content, f"overseer: reading — {title}")


def write_journal_entry(
    date: str = "",
    mood: int = 0,
    energy: int = 0,
    key_events: list[str] | None = None,
    gratitude: list[str] | None = None,
    body: str = "",
) -> str:
    tz = ZoneInfo(USER_TIMEZONE)
    if not date:
        date = datetime.now(tz).strftime("%Y-%m-%d")
    year, month, _ = date.split("-")
    path = f"wiki/journal/{year}/{month}/{date}.md"
    events_str = ", ".join(key_events or [])
    grat_str = ", ".join(gratitude or [])
    content = (
        f"---\ntitle: Journal {date}\npartition: journal\ntype: journal-entry\n"
        f"date: {date}\nmood: {mood}\nenergy: {energy}\n"
        f"key_events: [{events_str}]\ngratitude: [{grat_str}]\n"
        f"sources: [overseer]\ncreated: {date}\nupdated: {date}\ntags: [journal]\n---\n\n"
        f"# Journal — {date}\n\n{body}\n"
    )
    return vault_write(path, content, f"overseer: journal {date}")


def write_contact(
    name: str,
    org: str = "",
    role: str = "",
    relationship: str = "business",
    email: str = "",
    phone: str = "",
    notes: str = "",
) -> str:
    tz = ZoneInfo(USER_TIMEZONE)
    today = datetime.now(tz).strftime("%Y-%m-%d")
    subdir = {"business": "business", "vendor": "vendors", "supplier": "suppliers"}.get(relationship, "business")
    path = f"wiki/contacts/{subdir}/{_slug(name)}.md"
    content = (
        f"---\ntitle: {name}\npartition: contacts\ntype: contact\n"
        f"org: \"{org}\"\nrole: \"{role}\"\nrelationship: \"{relationship}\"\n"
        f"email: \"{email}\"\nphone: \"{phone}\"\nlast_contact: {today}\nnext_touch: \n"
        f"sources: [overseer]\ncreated: {today}\nupdated: {today}\ntags: [contacts, {relationship}]\n---\n\n"
        f"# {name}\n\n{notes}\n"
    )
    return vault_write(path, content, f"overseer: contact — {name}")


def write_idea(text: str, tags: list[str] | None = None) -> str:
    tz = ZoneInfo(USER_TIMEZONE)
    now = datetime.now(tz)
    stamp = now.strftime("%Y-%m-%dT%H%M")
    path = f"wiki/inbox-ideas/{stamp}-{_slug(text, 30)}.md"
    tag_str = ", ".join(tags or [])
    content = (
        f"---\ntitle: \"{text[:80]}\"\npartition: inbox-ideas\ntype: idea\n"
        f"captured_at: {now.strftime('%Y-%m-%dT%H:%M')}\ntriaged: false\ntarget_partition: \n"
        f"sources: [overseer]\ncreated: {now.strftime('%Y-%m-%d')}\nupdated: {now.strftime('%Y-%m-%d')}\n"
        f"tags: [inbox, ideas, {tag_str}]\n---\n\n"
        f"{text}\n"
    )
    return vault_write(path, content, f"overseer: idea — {text[:40]}")


def write_trip(
    destination: str,
    start_date: str,
    end_date: str = "",
    places: list[str] | None = None,
    itinerary: str = "",
    notes: str = "",
) -> str:
    tz = ZoneInfo(USER_TIMEZONE)
    today = datetime.now(tz).strftime("%Y-%m-%d")
    path = f"wiki/travel/trips/{start_date}-{_slug(destination)}.md"
    places_str = ", ".join(f"[[wiki/places/{p}]]" for p in (places or []))
    content = (
        f"---\ntitle: {destination} ({start_date})\npartition: travel\ntype: trip\n"
        f"destination: {destination}\nstart_date: {start_date}\nend_date: {end_date or start_date}\n"
        f"places: [{places_str}]\nitinerary: \"{itinerary[:200]}\"\n"
        f"sources: [overseer]\ncreated: {today}\nupdated: {today}\ntags: [travel]\n---\n\n"
        f"# {destination}\n\n{notes}\n"
    )
    return vault_write(path, content, f"overseer: trip — {destination}")


def quarantine_capture(raw_text: str, extracted_entities: list[str] | None = None, confidence_score: float = 0.0) -> str:
    """W10: park an input that doesn't fit any known partition.

    Brain grows new lobes when 3+ similar quarantined items show up — see
    scripts/novel_pattern_detector.py.
    """
    tz = ZoneInfo(USER_TIMEZONE)
    now = datetime.now(tz)
    stamp = now.strftime("%Y-%m-%dT%H%M")
    path = f"wiki/inbox-novel/{stamp}-{_slug(raw_text, 30)}.md"
    entities_str = ", ".join(extracted_entities or [])
    content = (
        f"---\ntitle: \"{raw_text[:80]}\"\npartition: inbox-novel\ntype: novel-capture\n"
        f"captured_at: {now.strftime('%Y-%m-%dT%H:%M')}\n"
        f"extracted_entities: [{entities_str}]\nconfidence_score: {confidence_score}\n"
        f"triaged: false\ntarget_partition: \n"
        f"sources: [overseer]\ncreated: {now.strftime('%Y-%m-%d')}\nupdated: {now.strftime('%Y-%m-%d')}\n"
        f"tags: [inbox, novel]\n---\n\n"
        f"{raw_text}\n"
    )
    return vault_write(path, content, f"overseer: novel — {raw_text[:40]}")


TOOLS_MAP = {
    "vault_read": vault_read,
    "vault_write": vault_write,
    "vault_search": vault_search,
    "list_notes": list_notes,
    "vault_inbox_query": vault_inbox_query,
    "write_calendar_event": write_calendar_event,
    "write_health_daily": write_health_daily,
    "write_place_visit": write_place_visit,
    "write_finance_transaction": write_finance_transaction,
    "write_todo": write_todo,
    "update_wiki_page": update_wiki_page,
    "write_deal": write_deal,
    "write_lead": write_lead,
    "write_client": write_client,
    "write_bookmark": write_bookmark,
    "write_reading_item": write_reading_item,
    "write_journal_entry": write_journal_entry,
    "write_contact": write_contact,
    "write_idea": write_idea,
    "write_trip": write_trip,
    "quarantine_capture": quarantine_capture,
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
        "name": "vault_inbox_query",
        "description": "Query recent activity clusters from the vault inbox (distilled by Sleipnir). Returns what Sam was working on.",
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
    {"type": "function", "function": {
        "name": "write_deal",
        "description": "Add a sales deal to wiki/sales/deals/. Use when Sam mentions a new opportunity, deal status change, or pipeline update.",
        "parameters": {
            "type": "object",
            "properties": {
                "client": {"type": "string"},
                "status": {"type": "string", "description": "lead|qualified|proposal|negotiation|won|lost"},
                "value": {"type": "number"},
                "currency": {"type": "string"},
                "close_date": {"type": "string", "description": "YYYY-MM-DD"},
                "next_action": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["client"],
        },
    }},
    {"type": "function", "function": {
        "name": "write_lead",
        "description": "Add a sales lead to wiki/sales/leads/.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "source": {"type": "string"},
                "status": {"type": "string", "description": "cold|warm|qualified"},
                "notes": {"type": "string"},
            },
            "required": ["name"],
        },
    }},
    {"type": "function", "function": {
        "name": "write_client",
        "description": "Add a closed-won client to wiki/sales/clients/.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "mrr": {"type": "number"},
                "relationship": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["name"],
        },
    }},
    {"type": "function", "function": {
        "name": "write_bookmark",
        "description": "Save a URL to wiki/bookmarks/. Use when Sam shares a link or asks you to save one.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "title": {"type": "string"},
                "topic": {"type": "string"},
                "source": {"type": "string"},
                "summary": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["url"],
        },
    }},
    {"type": "function", "function": {
        "name": "write_reading_item",
        "description": "Add a book/article/course/podcast to wiki/reading/.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "medium": {"type": "string", "description": "book|article|course|podcast"},
                "author": {"type": "string"},
                "status": {"type": "string", "description": "queue|reading|done|abandoned"},
                "progress_pct": {"type": "integer"},
                "notes": {"type": "string"},
            },
            "required": ["title"],
        },
    }},
    {"type": "function", "function": {
        "name": "write_journal_entry",
        "description": "Create or update a daily journal entry in wiki/journal/YYYY/MM/. Use when Sam describes how a day went or reflects on mood/energy.",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD (default today)"},
                "mood": {"type": "integer", "description": "1-10"},
                "energy": {"type": "integer", "description": "1-10"},
                "key_events": {"type": "array", "items": {"type": "string"}},
                "gratitude": {"type": "array", "items": {"type": "string"}},
                "body": {"type": "string"},
            },
        },
    }},
    {"type": "function", "function": {
        "name": "write_contact",
        "description": "Add a business contact/vendor/supplier to wiki/contacts/. Distinct from personal/people/ which holds family/close-friends only.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "org": {"type": "string"},
                "role": {"type": "string"},
                "relationship": {"type": "string", "description": "business|vendor|supplier"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["name"],
        },
    }},
    {"type": "function", "function": {
        "name": "write_idea",
        "description": "Quick-capture an idea to wiki/inbox-ideas/ for later triage. Use for half-formed thoughts that map to a partition but aren't ready to file.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["text"],
        },
    }},
    {"type": "function", "function": {
        "name": "write_trip",
        "description": "Add a trip to wiki/travel/trips/.",
        "parameters": {
            "type": "object",
            "properties": {
                "destination": {"type": "string"},
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string"},
                "places": {"type": "array", "items": {"type": "string"}},
                "itinerary": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["destination", "start_date"],
        },
    }},
    {"type": "function", "function": {
        "name": "quarantine_capture",
        "description": "Park an input that doesn't fit any existing partition. Use ONLY when Sam tells you something that doesn't map to known schemas — confirm with Sam first ('I don't have a home for this — parking in inbox-novel/, ok?'). Brain grows new lobes from accumulated quarantine items.",
        "parameters": {
            "type": "object",
            "properties": {
                "raw_text": {"type": "string"},
                "extracted_entities": {"type": "array", "items": {"type": "string"}},
                "confidence_score": {"type": "number"},
            },
            "required": ["raw_text"],
        },
    }},
]


# ─── LLM providers ───────────────────────────────────────────────────────────

async def _ollama_request(url: str, messages: list[dict]) -> dict:
    ctx_opts = {"num_ctx": int(os.environ.get("OLLAMA_CTX", "4096"))}
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(f"{url}/api/chat", json={
            "model": OLLAMA_MODEL,
            "messages": messages,
            "tools": TOOLS_SPEC,
            "stream": False,
            "options": ctx_opts,
        })
        if r.status_code == 400:
            r = await client.post(f"{url}/api/chat", json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": False,
                "options": ctx_opts,
            })
        r.raise_for_status()
    data = r.json()
    msg = data.get("message", {})
    return {
        "content": msg.get("content") or "",
        "tool_calls": msg.get("tool_calls") or [],
    }


async def ollama_chat(messages: list[dict]) -> dict:
    try:
        return await _ollama_request(OLLAMA_URL, messages)
    except Exception as e:
        fallback = "http://localhost:11434"
        if OLLAMA_URL != fallback:
            logging.warning("ollama at %s failed (%s), falling back to %s", OLLAMA_URL, e, fallback)
            return await _ollama_request(fallback, messages)
        raise


async def _stream_ollama(url: str, messages: list[dict], ctx_opts: dict):
    """Async generator yielding (content, tool_calls, stats_or_None) tuples."""
    async with httpx.AsyncClient(timeout=180) as client:
        body = {
            "model": OLLAMA_MODEL,
            "messages": messages,
            "tools": TOOLS_SPEC,
            "stream": True,
            "options": ctx_opts,
        }
        tools_ok = True
        async with client.stream("POST", f"{url}/api/chat", json=body) as resp:
            if resp.status_code == 400:
                tools_ok = False
            else:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    msg = data.get("message", {})
                    stats = None
                    if data.get("done"):
                        stats = {"eval_count": data.get("eval_count", 0), "eval_duration": data.get("eval_duration", 0)}
                    yield msg.get("content") or "", msg.get("tool_calls") or [], stats

        if not tools_ok:
            body2 = {k: v for k, v in body.items() if k != "tools"}
            async with client.stream("POST", f"{url}/api/chat", json=body2) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    stats = None
                    if data.get("done"):
                        stats = {"eval_count": data.get("eval_count", 0), "eval_duration": data.get("eval_duration", 0)}
                    yield data.get("message", {}).get("content") or "", [], stats


def _filter_args(tool_fn, args: dict) -> tuple[dict, list[str]]:
    """Keep only args the function actually accepts. Returns (filtered, dropped_names).

    Small models often invent field names (e.g. 'description' instead of 'summary').
    Silently dropping unknowns and logging is more useful than raising TypeError.
    """
    import inspect
    try:
        sig = inspect.signature(tool_fn)
    except (TypeError, ValueError):
        return args, []
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return args, []
    allowed = set(sig.parameters.keys())
    kept = {k: v for k, v in args.items() if k in allowed}
    dropped = [k for k in args if k not in allowed]
    return kept, dropped


async def run_tool_loop(messages: list[dict]) -> tuple[str, list[str]]:
    tool_log: list[str] = []
    for _ in range(12):
        response = await ollama_chat(messages)
        content = response["content"]
        tool_calls = response["tool_calls"]

        # Text-based tool calling fallback (@@TOOL@@ protocol for models that ignore native tools)
        text_tool_calls = re.findall(r'^@@TOOL@@(\{.+\})', content, re.MULTILINE)
        if text_tool_calls and not tool_calls:
            for raw in text_tool_calls:
                try:
                    data = json.loads(raw)
                    tool_name = data.pop("t", None)
                    if tool_name and tool_name in TOOLS_MAP:
                        tool_fn = TOOLS_MAP[tool_name]
                        kept, dropped = _filter_args(tool_fn, data)
                        if dropped:
                            logging.info("text tool %s: dropped unknown args %s", tool_name, dropped)
                        short_args = ", ".join(f"{k}={repr(v)[:40]}" for k, v in kept.items())
                        tool_log.append(f"{tool_name}({short_args})")
                        try:
                            tool_fn(**kept)
                        except Exception as e:
                            logging.warning("text tool error %s: %s", tool_name, e)
                except (json.JSONDecodeError, TypeError, KeyError) as e:
                    logging.warning("text tool parse: %s in %r", e, raw)
            clean = re.sub(r'^@@TOOL@@\{.+\}\n?', '', content, flags=re.MULTILINE).strip()
            return clean, tool_log

        if not tool_calls:
            return content, tool_log

        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            raw_args = fn.get("arguments", {})
            args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})

            tool_fn = TOOLS_MAP.get(name)
            if tool_fn:
                kept, dropped = _filter_args(tool_fn, args)
                if dropped:
                    logging.info("tool %s: dropped unknown args %s", name, dropped)
            else:
                kept = args

            short_args = ", ".join(f"{k}={repr(v)[:40]}" for k, v in kept.items())
            tool_log.append(f"{name}({short_args})")

            if tool_fn:
                try:
                    result = tool_fn(**kept)
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

def _read_urchin_journal(hours: int = 6) -> str:
    """Read recent events directly from the Urchin journal JSONL file."""
    if not URCHIN_JOURNAL.exists():
        return ""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    NOISE = {"", "ls", "pwd", "clear", "exit", "history"}
    SOURCE_LABELS = {
        "shell": "shell", "git": "git", "browser": "web",
        "vscode": "editor", "copilot": "ai", "claude": "ai",
    }
    events: list[tuple[datetime, str, str]] = []
    try:
        # Read tail of file efficiently — scan last 200KB
        size = URCHIN_JOURNAL.stat().st_size
        seek = max(0, size - 200_000)
        with open(URCHIN_JOURNAL, "r", errors="replace") as f:
            if seek:
                f.seek(seek)
                f.readline()  # skip partial line
            for line in f:
                try:
                    e = json.loads(line)
                    ts_raw = e.get("timestamp", "")
                    if not ts_raw:
                        continue
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    if ts < cutoff:
                        continue
                    source = e.get("source", "")
                    content = (e.get("content") or "").strip()
                    if not content or content in NOISE or content.startswith("#"):
                        continue
                    label = SOURCE_LABELS.get(source, source)
                    events.append((ts, label, content[:120]))
                except Exception:
                    pass
    except Exception:
        return ""
    if not events:
        return ""
    # Deduplicate consecutive identical content
    deduped = []
    prev = None
    for ts, label, content in events:
        if content != prev:
            deduped.append((ts, label, content))
            prev = content
    # Group into a readable summary
    lines = [f"[{label}] {content}" for _, label, content in deduped[-40:]]
    return "\n".join(lines)


def _recent_activity_context() -> str:
    # Prefer Sleipnir clusters (distilled, semantically grouped)
    if SLEIPNIR_DB.exists():
        activity = vault_inbox_query(hours=6)
        if activity and "[" not in activity:
            return f"\nRecent activity (Sleipnir, last 6h):\n{activity}\n"
    # Fall back to raw Urchin journal
    raw = _read_urchin_journal(hours=6)
    if raw:
        return f"\nRecent activity (Urchin journal, last 6h):\n{raw}\n"
    return ""


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


def _load_people_index() -> str:
    """Build a compact index of known people from wiki/personal/people/*.md.

    Injected into the system prompt so Overseer never invents relationships.
    Format: one line per person — slug | name | relationship (if known).
    Pages flagged sources: [synthesized] are skipped — those are hallucination residue.
    """
    people_dir = VAULT_PATH / "wiki" / "personal" / "people"
    if not people_dir.exists():
        return "[no people indexed yet]"
    lines: list[str] = []
    for f in sorted(people_dir.glob("*.md")):
        if f.name.startswith("_") or f.stem == "index":
            continue
        try:
            text = f.read_text()
        except Exception:
            continue
        if "sources: [synthesized]" in text or "sources:\n  - synthesized" in text:
            continue  # skip hallucination residue
        name = f.stem
        rel = ""
        for line in text.splitlines()[:20]:
            line = line.strip()
            if line.startswith("name:"):
                name = line.split(":", 1)[1].strip() or name
            elif line.startswith("relationship:"):
                rel = line.split(":", 1)[1].strip()
        suffix = f" — {rel}" if rel else ""
        lines.append(f"- {f.stem}: {name}{suffix}")
    return "\n".join(lines) if lines else "[no people indexed yet]"


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


def _load_novel_proposals() -> str:
    """W10 — surface pending cluster-genesis proposals from novel_pattern_detector."""
    proposals_file = VAULT_PATH / "wiki" / "inbox-novel" / "_proposals.md"
    if not proposals_file.exists():
        return ""
    try:
        text = proposals_file.read_text()
    except Exception:
        return ""
    # strip frontmatter, return body
    if text.startswith("---"):
        end = text.find("\n---", 4)
        if end >= 0:
            text = text[end + 4:].lstrip()
    if "No patterns yet" in text:
        return ""
    return text[:1500]


def build_system_prompt() -> str:
    tz = ZoneInfo(USER_TIMEZONE)
    now_str = datetime.now(tz).strftime("%Y-%m-%d, %A, %H:%M %Z")
    facts = _load_facts()
    people = _load_people_index()
    proposals = _load_novel_proposals()

    return f"""You are Overseer, Sam's personal AI assistant — his Jarvis. Sam is the person talking to you. You are not Sam.

YOUR IDENTITY:
You are Sam's personal project. You are NOT a Mad House project. You are NOT an Orinadus project. Overseer exists to help Sam organize his life so he can build those (separate) companies.

ABOUT SAM:
Sam Charles (Samuel), 21, Capitol Hill Seattle. Founder of Orinadus. Creator of Mad House.
Mad House and Orinadus are FULLY SEPARATE companies with no relation, no shared IP, no parent/child. Never blend them or speak of them as one.
- Orinadus owns Urchin (raw event ingestion infra) and Sleipnir (distillation infra). GitHub org: orinadus-systems.
- Mad House is a separate studio with its own brand and projects.

VAULT:
Sam's vault at ~/vault/ is his second brain — the source of truth for everything personal: people, relationships, calendar, health, finance, places, projects. Schema is in `VAULT.md`. You read it. You write it. Never invent facts about Sam's life that aren't grounded in vault data.

TOOL PROTOCOL:
Call tools by outputting this exact format on its own line (nothing before @@TOOL@@):
@@TOOL@@{{"t":"tool_name","key":"value"}}

Examples (only invoke when actually relevant — these reference real vault paths and shapes):
@@TOOL@@{{"t":"write_calendar_event","date":"2026-05-25","title":"Meeting with Armond","time":"10:00","duration_mins":120,"location":"621 12th Ave E, Seattle WA"}}
@@TOOL@@{{"t":"write_todo","task":"Call dentist","due_date":"2026-05-25"}}
@@TOOL@@{{"t":"vault_search","query":"Armond"}}
@@TOOL@@{{"t":"vault_read","path":"wiki/personal/people/mum.md"}}

Rules:
- Full JSON on that single line, starting exactly with @@TOOL@@ — no leading spaces
- No announcement before calling — just output the @@TOOL@@ line, then confirm briefly after
- Call immediately when Sam tells you:
  · meeting/event → write_calendar_event
  · task → write_todo
  · health data → write_health_daily
  · purchase → write_finance_transaction
  · sales deal/opportunity → write_deal (lead/qualified/proposal/negotiation/won/lost)
  · new lead → write_lead; closed-won client → write_client
  · URL to save → write_bookmark
  · book/article/course/podcast → write_reading_item
  · day reflection / mood / "today was…" → write_journal_entry
  · business contact / vendor → write_contact (NOT vault_write — contacts/ partition is distinct from personal/people/)
  · half-formed thought → write_idea (parks in inbox-ideas/ for later triage)
  · trip / travel plan → write_trip
  · person info (family, close friends) → vault_write to wiki/personal/people/ (read first)
  · project update → vault_write to wiki/projects/ or canonical company partition (read first)
- When unsure if something is already recorded: vault_search first

OUT-OF-SCHEMA INPUTS (W10 — brain grows new lobes):
- If Sam tells you something that doesn't match any tool above and doesn't fit any partition listed in VAULT.md, do NOT shoehorn it into the closest match.
- Instead, say ONE line: "I don't have a home for this — parking in inbox-novel/, ok?" then wait for Sam's ack.
- On ack (y/Enter): call quarantine_capture with the raw text.
- On "n" or alternative direction: follow Sam's lead.
- Never invent a new partition or schema on your own; quarantine_capture is the only path for off-schema inputs.

ANTI-HALLUCINATION (critical):
- NEVER invent a person, relationship, place, or fact about Sam. If you don't know, you don't know.
- If Sam mentions a person you don't recognise, vault_search the name first. If still no record, ask Sam — do NOT guess a relationship.
- Tag every personal claim with its source: [vault: <path>] when sourced from the vault, [inferred] when reasoning beyond vault data, [unknown] when you have no source. Do this inline in your response.
- Examples in this system prompt are SHAPES for tool calls, not facts about Sam. Never echo example data as if it were real.

VAULT RULES:
- Before vault_write: always call vault_read first to merge, never overwrite cold.
- vault_search before writing new knowledge pages.
- Every wiki page needs a `sources:` frontmatter field. Pages with `sources: [synthesized]` are flagged as hallucination risk.

VOICE:
- Short. 1-3 sentences unless Sam asks for more.
- No filler phrases. No "Certainly!" or "I've noted that". Just talk.
- Casual input gets casual response. Don't search the vault for "whatsup".

CONTEXT (when Sam asks about activity or history):
- vault_inbox_query: what Sam was working on recently (distilled activity clusters in the vault inbox)
- vault_search: search the vault
- vault_read: read a specific page

PEOPLE SAM KNOWS (from vault — only assert relationships listed here; if Sam mentions someone not on this list, vault_search first, then ask if still unknown):
{people}

FACTS (from memory):
{facts}

{f'PENDING NOVEL-CLUSTER PROPOSALS (surface ONCE early in the session, ask Sam if he wants any of these new partitions created — never auto-create):\n{proposals}\n' if proposals else ''}
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




# ─── request models ───────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None


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

    return {
        "response": answer,
        "tool_calls": tool_log,
        "thread_id": thread_id,
        "model": OLLAMA_MODEL,
    }


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    thread_id = req.thread_id or "default"
    history = load_thread(thread_id)

    messages = [{"role": "system", "content": system_prompt()}]
    messages.extend(history[-20:])
    messages.append({"role": "user", "content": req.message})

    async def generate():
        tool_log: list[str] = []
        full_response = ""
        ctx_opts = {"num_ctx": int(os.environ.get("OLLAMA_CTX", "4096"))}
        fallback = "http://localhost:11434"
        urls = [OLLAMA_URL] if OLLAMA_URL == fallback else [OLLAMA_URL, fallback]
        total_eval_count = 0
        total_eval_duration = 0

        for _ in range(12):
            collected_content = ""
            collected_tool_calls: list = []
            text_tool_lines: list[str] = []
            ok = False

            # Tool marker may arrive split across chunks; buffer until we know
            # whether `@@TOOL@@{...}` is complete (balanced braces) or absent.
            tool_re = re.compile(r"@@TOOL@@(\{[^\n]*?\})", re.DOTALL)
            partial_marker_re = re.compile(r"@+T?O?O?L?@*\{?[^@\n]*$")

            for url in urls:
                try:
                    pending = ""
                    flush_safe = ""

                    def split_pending(buf: str) -> tuple[str, str]:
                        """Return (safe_to_flush, hold_back). Hold back any prefix that
                        could still grow into a @@TOOL@@{...} marker."""
                        # Strip complete tool markers anywhere in buf
                        out_parts: list[str] = []
                        i = 0
                        for m in tool_re.finditer(buf):
                            out_parts.append(buf[i:m.start()])
                            text_tool_lines.append("@@TOOL@@" + m.group(1))
                            i = m.end()
                        rest = buf[i:]
                        # Find latest position where a partial @@TOOL@@... marker could begin
                        m2 = partial_marker_re.search(rest)
                        if m2:
                            out_parts.append(rest[:m2.start()])
                            hold = rest[m2.start():]
                        else:
                            out_parts.append(rest)
                            hold = ""
                        return "".join(out_parts), hold

                    async for content, tcs, stats in _stream_ollama(url, messages, ctx_opts):
                        if content:
                            pending += content
                            flush_safe, pending = split_pending(pending)
                            if flush_safe:
                                collected_content += flush_safe
                                yield f"data: {json.dumps({'type': 'chunk', 'content': flush_safe})}\n\n"
                        if tcs:
                            collected_tool_calls.extend(tcs)
                        if stats:
                            total_eval_count += stats.get("eval_count", 0)
                            total_eval_duration += stats.get("eval_duration", 0)
                    # End of stream — flush whatever's still pending (must be safe now)
                    if pending:
                        flush_safe, leftover = split_pending(pending + "\n")
                        if flush_safe.rstrip("\n"):
                            chunk = flush_safe.rstrip("\n")
                            collected_content += chunk
                            yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"
                        _ = leftover  # any unmatched partial marker is dropped (model truncation)
                    ok = True
                    break
                except Exception as e:
                    logging.warning("stream from %s failed: %s", url, e)

            if not ok:
                yield f"data: {json.dumps({'type': 'error', 'content': 'ollama unreachable'})}\n\n"
                return

            for tool_line in text_tool_lines:
                raw = tool_line[len("@@TOOL@@"):].strip()
                tool_name = None
                data: dict | None = None
                parse_err: str | None = None

                # Try strict JSON first
                try:
                    data = json.loads(raw)
                    if isinstance(data, dict):
                        tool_name = data.pop("t", None)
                except (json.JSONDecodeError, TypeError):
                    parse_err = "invalid json"

                # Tolerant fallback: small models sometimes emit positional-style
                # `{"tool_name","arg1","arg2"}` instead of `{"t":"name","key":"val"}`.
                # Try to recover the tool name and treat remaining strings as a
                # single `query` argument so vault_search etc. still work.
                if data is None or not tool_name:
                    try:
                        tokens = re.findall(r'"([^"]*)"', raw)
                        if tokens:
                            candidate = tokens[0]
                            if candidate in TOOLS_MAP:
                                tool_name = candidate
                                data = {}
                                if len(tokens) > 1:
                                    data["query"] = " ".join(tokens[1:])
                                parse_err = None
                    except Exception as e:
                        parse_err = parse_err or str(e)

                if tool_name and tool_name in TOOLS_MAP and data is not None:
                    short_args = ", ".join(f"{k}={repr(v)[:35]}" for k, v in data.items())
                    tool_log.append(f"{tool_name}({short_args})")
                    yield f"data: {json.dumps({'type': 'tool', 'name': tool_name, 'args': short_args})}\n\n"
                    try:
                        result = TOOLS_MAP[tool_name](**data)
                    except Exception as e:
                        result = f"[tool error: {e}]"
                    preview = str(result)[:80].replace("\n", " ")
                    yield f"data: {json.dumps({'type': 'tool_done', 'name': tool_name, 'preview': preview})}\n\n"
                else:
                    # Even malformed attempts get surfaced so the user sees
                    # the model TRIED to call something — better than silence.
                    name = tool_name or "(unparseable)"
                    err = f"malformed call: {parse_err or 'unknown tool'}"
                    short_args = trunc_str(raw, 70)
                    tool_log.append(f"{name}({short_args})")
                    yield f"data: {json.dumps({'type': 'tool', 'name': name, 'args': short_args})}\n\n"
                    yield f"data: {json.dumps({'type': 'tool_done', 'name': name, 'preview': 'error: ' + err})}\n\n"
                    logging.warning("text tool parse error: %s in %r", parse_err, tool_line)

            full_response += collected_content

            if not collected_tool_calls:
                break

            messages.append({"role": "assistant", "content": collected_content, "tool_calls": collected_tool_calls})

            for tc in collected_tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                raw_args = fn.get("arguments", {})
                args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                short_args = ", ".join(f"{k}={repr(v)[:35]}" for k, v in args.items())
                tool_log.append(f"{name}({short_args})")

                yield f"data: {json.dumps({'type': 'tool', 'name': name, 'args': short_args})}\n\n"

                tool_fn = TOOLS_MAP.get(name)
                if tool_fn:
                    try:
                        result = tool_fn(**args)
                    except Exception as e:
                        result = f"[tool error: {e}]"
                else:
                    result = f"[unknown tool: {name}]"

                preview = str(result)[:80].replace("\n", " ")
                yield f"data: {json.dumps({'type': 'tool_done', 'name': name, 'preview': preview})}\n\n"

                tool_msg: dict = {"role": "tool", "content": str(result)[:8000]}
                if "id" in tc:
                    tool_msg["tool_call_id"] = tc["id"]
                messages.append(tool_msg)

        history.append({"role": "user", "content": req.message})
        history.append({"role": "assistant", "content": full_response})
        save_thread(thread_id, history)

        tps = round(total_eval_count / total_eval_duration * 1e9, 1) if total_eval_duration > 0 else 0.0
        yield f"data: {json.dumps({'type': 'done', 'model': OLLAMA_MODEL, 'tool_calls': tool_log, 'tokens': total_eval_count, 'tps': tps})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


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


# ─── gateway node protocol ────────────────────────────────────────────────────
# The gateway calls /infer/chat with the user message + conversation history.
# This node builds its own system prompt (with live vault context) and infers.

class InferRequest(BaseModel):
    message: str
    history: list[dict] = []


@app.post("/infer/chat")
async def infer_chat(req: InferRequest, request: Request):
    if GATEWAY_NODE_SECRET:
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

    return {"content": answer, "tool_calls": tool_log, "model": OLLAMA_MODEL}


@app.on_event("startup")
async def startup():
    pass  # Ollama loads model on first request
