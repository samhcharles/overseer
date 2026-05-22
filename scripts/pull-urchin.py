#!/usr/bin/env python3
"""
pull-urchin.py: pull recent events from the local Urchin journal into vault.

Run from WSL where Urchin is running on 127.0.0.1:18799.

Usage:
  python3 pull-urchin.py             # pull last 24h, commit to vault
  python3 pull-urchin.py --hours 72  # pull last 72h
  python3 pull-urchin.py --dry-run   # show what would be written, no commit
"""

import argparse
import json
import os
import sys
import subprocess
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

URCHIN_URL = os.environ.get("URCHIN_URL", "http://127.0.0.1:18799")
VAULT_DIR  = Path(os.environ.get("VAULT_PATH", Path.home() / "vault"))
CURSOR_FILE = Path.home() / ".local" / "share" / "urchin" / "overseer-cursor.txt"

# Map EventKind to vault destination path (relative to VAULT_DIR)
KIND_PATHS = {
    "health_metric":  "wiki/personal/health/{ym}.md",
    "purchase":       "wiki/personal/purchases/{ym}.md",
    "calendar_event": "wiki/personal/calendar/{ym}.md",
    "location":       "wiki/personal/location/{ym}.md",
    "search_query":   "wiki/personal/search/{ym}.md",
    "watch_history":  "wiki/personal/watch/{ym}.md",
    "conversation":   "wiki/systems/dev-activity/{ym}.md",
    "command":        "wiki/systems/dev-activity/{ym}.md",
    "commit":         "wiki/systems/dev-activity/{ym}.md",
    "agent":          "wiki/systems/dev-activity/{ym}.md",
}

DEFAULT_PATH = "wiki/systems/dev-activity/{ym}.md"


def fetch_events(hours: float, limit: int = 500) -> list[dict]:
    url = f"{URCHIN_URL}/recent?hours={hours}&n={limit}"
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=5) as resp:
            data = json.load(resp)
            return data.get("events", [])
    except URLError as e:
        print(f"error: cannot reach Urchin at {URCHIN_URL}: {e}", file=sys.stderr)
        print("Is the Urchin daemon running? Try: urchin serve", file=sys.stderr)
        sys.exit(1)


def load_cursor() -> str:
    if CURSOR_FILE.exists():
        return CURSOR_FILE.read_text().strip()
    return ""


def save_cursor(ts: str) -> None:
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(ts)


def ym(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m")
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y-%m")


def vault_path(kind: str, ts: str) -> Path:
    template = KIND_PATHS.get(kind, DEFAULT_PATH)
    rel = template.format(ym=ym(ts))
    return VAULT_DIR / rel


def format_entry(event: dict) -> str:
    ts = event.get("timestamp", "")
    source = event.get("source", "")
    kind = event.get("kind", "")
    content = event.get("content", "")

    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        time_str = dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        time_str = ts

    meta = event.get("meta") or {}
    notes = []
    if meta.get("amount"):
        currency = meta.get("currency", "USD")
        notes.append(f"${abs(meta['amount']):.2f} {currency}")
    if meta.get("merchant"):
        notes.append(f"at {meta['merchant']}")
    if meta.get("value") and meta.get("unit"):
        notes.append(f"{meta['value']} {meta['unit']}")
    if meta.get("duration_secs"):
        mins = int(meta["duration_secs"]) // 60
        notes.append(f"{mins}min")
    if meta.get("attendees"):
        notes.append(f"{meta['attendees']} attendees")
    if meta.get("lat") and meta.get("lng"):
        notes.append(f"({meta['lat']:.4f}, {meta['lng']:.4f})")

    line = f"- `{time_str}` [{source}] {content}"
    if notes:
        line += f" ({', '.join(notes)})"
    return line


def section_heading(kind: str, ym_str: str) -> str:
    month = datetime.strptime(ym_str, "%Y-%m").strftime("%B %Y")
    headings = {
        "health_metric":  f"# Health: {month}",
        "purchase":       f"# Purchases: {month}",
        "calendar_event": f"# Calendar: {month}",
        "location":       f"# Location: {month}",
        "search_query":   f"# Search: {month}",
        "watch_history":  f"# Watch History: {month}",
    }
    return headings.get(kind, f"# Dev Activity: {month}")


def write_events_to_vault(events: list[dict], dry_run: bool) -> dict[str, int]:
    by_file: dict[str, list[str]] = defaultdict(list)

    for event in events:
        kind = event.get("kind", "other")
        ts   = event.get("timestamp", "")
        path = vault_path(kind, ts)
        line = format_entry(event)
        by_file[str(path)].append((kind, ym(ts), line))

    written = {}
    for filepath, entries in by_file.items():
        path = Path(filepath)
        kind = entries[0][0]
        ym_str = entries[0][1]

        if dry_run:
            print(f"\n{filepath}:")
            for _, _, line in entries:
                print(f"  {line}")
            written[filepath] = len(entries)
            continue

        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text() if path.exists() else ""

        heading = section_heading(kind, ym_str)
        if heading not in existing:
            existing = existing.rstrip("\n") + f"\n\n{heading}\n\n" if existing else f"{heading}\n\n"

        new_lines = "\n".join(line for _, _, line in entries)
        content = existing.rstrip("\n") + "\n" + new_lines + "\n"

        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        written[filepath] = len(entries)

    return written


def git_commit(vault_dir: Path, files: list[str]) -> None:
    rel_files = [str(Path(f).relative_to(vault_dir)) for f in files]
    subprocess.run(["git", "-C", str(vault_dir), "add"] + rel_files, check=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    msg = f"urchin-pull: {len(rel_files)} file(s) updated {ts}"
    subprocess.run(["git", "-C", str(vault_dir), "commit", "-m", msg], check=True)
    subprocess.Popen(["git", "-C", str(vault_dir), "push", "origin", "main"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull Urchin events into vault")
    parser.add_argument("--hours",   type=float, default=24,  help="Look back N hours (default 24)")
    parser.add_argument("--limit",   type=int,   default=500, help="Max events to pull (default 500)")
    parser.add_argument("--dry-run", action="store_true",     help="Print what would be written, no commit")
    parser.add_argument("--no-commit", action="store_true",   help="Write files but skip git commit")
    args = parser.parse_args()

    if not VAULT_DIR.exists():
        print(f"error: vault not found at {VAULT_DIR}", file=sys.stderr)
        print("Run vault-init to set up the vault.", file=sys.stderr)
        sys.exit(1)

    events = fetch_events(args.hours, args.limit)
    if not events:
        print("no new events")
        return

    cursor = load_cursor()
    new_events = [e for e in events if e.get("timestamp", "") > cursor]
    if not new_events:
        print(f"no events newer than cursor {cursor!r}")
        return

    print(f"pulling {len(new_events)} event(s) (hours={args.hours})")

    written = write_events_to_vault(new_events, dry_run=args.dry_run)
    if not written:
        print("nothing written")
        return

    total = sum(written.values())
    for filepath, count in written.items():
        print(f"  {count} event(s) -> {filepath}")

    if args.dry_run:
        print(f"\n[dry-run] would write {total} event(s) across {len(written)} file(s)")
        return

    newest_ts = max(e.get("timestamp", "") for e in new_events)
    save_cursor(newest_ts)

    if not args.no_commit:
        try:
            git_commit(VAULT_DIR, list(written.keys()))
            print(f"committed and pushed ({total} event(s))")
        except subprocess.CalledProcessError as e:
            print(f"warning: git commit failed: {e}", file=sys.stderr)
    else:
        print(f"wrote {total} event(s) (no commit)")


if __name__ == "__main__":
    main()
