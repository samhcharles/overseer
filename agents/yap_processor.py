#!/usr/bin/env python3
"""
yap_processor.py — processes raw yap files from vault/inbox/yap/.
Called when a new yap file appears (by tagger or directly).
Sends to Overseer /chat with entity extraction instructions,
routes extracted facts to memory/ and wiki/personal/.
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import httpx

VAULT_PATH = Path(os.environ.get("VAULT_PATH", Path.home() / "vault"))
OVERSEER_API_URL = os.environ.get("OVERSEER_API_URL", "")


def vault_read(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def vault_append(path: Path, text: str, commit_msg: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(text)
    try:
        subprocess.run(["git", "-C", str(VAULT_PATH), "add", str(path)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(VAULT_PATH), "commit", "-m", commit_msg], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(VAULT_PATH), "push", "origin", "main"], capture_output=True)
    except subprocess.CalledProcessError:
        pass


def overseer_chat(message: str) -> str:
    if not OVERSEER_API_URL:
        return ""
    try:
        r = httpx.post(f"{OVERSEER_API_URL}/chat", json={"message": message}, timeout=60)
        r.raise_for_status()
        return r.json().get("response", "")
    except Exception as e:
        print(f"[yap] overseer error: {e}", file=sys.stderr)
        return ""


def process_yap(yap_file: Path) -> None:
    content = yap_file.read_text()
    today = datetime.now().strftime("%Y-%m-%d")
    timestamp = datetime.now().strftime("%H:%M")

    print(f"[yap] processing {yap_file.name}")

    prompt = f"""The user recorded this yap (voice/text dump):

---
{content[:4000]}
---

Extract and act on all entities. For each:
1. NEW PERSON mentioned: use vault_write to create wiki/personal/people/[name].md
2. BIRTHDAY or PERSONAL FACT about someone: use vault_write to append to memory/facts/people.md
3. PREFERENCE or DEFAULT choice: append to memory/facts/preferences.md
4. RECURRING EVENT or SCHEDULE: append to memory/facts/recurring.md
5. PROJECT UPDATE: append to the relevant wiki/madhouse/ or wiki/orinadus/ page

After writing to vault, return a brief summary of what you stored and where.
Do not fabricate — only extract what is explicitly stated."""

    summary = overseer_chat(prompt)

    # Append processing record to daily note
    daily_file = VAULT_PATH / "daily" / f"{today}.md"
    entry = (
        f"\n<!-- tagger:yap:{timestamp} -->\n"
        f"**Yap processed:** {yap_file.name}\n"
        f"{summary}\n"
        f"<!-- /tagger:yap -->\n"
    )
    vault_append(daily_file, entry, f"yap: processed {yap_file.name} → {today} daily")

    if summary:
        print(f"[yap] {summary[:100]}")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: yap_processor.py <yap_file.md>", file=sys.stderr)
        sys.exit(1)

    yap_file = Path(sys.argv[1])
    if not yap_file.exists():
        print(f"[yap] file not found: {yap_file}", file=sys.stderr)
        sys.exit(1)

    process_yap(yap_file)


if __name__ == "__main__":
    main()
