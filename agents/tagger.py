#!/usr/bin/env python3
"""
tagger.py — watches vault/inbox/ for new files, calls Overseer /triage,
routes organized summary to the appropriate wiki/ location.
Runs as a long-lived process via systemd or screen.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

VAULT_PATH = Path(os.environ.get("VAULT_PATH", Path.home() / "vault"))
OVERSEER_API_URL = os.environ.get("OVERSEER_API_URL", "")
POLL_INTERVAL = int(os.environ.get("TAGGER_POLL_INTERVAL", "30"))
SEEN_FILE = Path.home() / ".local" / "state" / "brain-agents" / "tagger-seen.json"


def load_seen() -> set[str]:
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen(seen: set[str]) -> None:
    SEEN_FILE.write_text(json.dumps(sorted(seen)))


def vault_commit(path: Path, msg: str) -> None:
    try:
        subprocess.run(["git", "-C", str(VAULT_PATH), "add", str(path)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(VAULT_PATH), "commit", "-m", msg], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(VAULT_PATH), "push", "origin", "main"], capture_output=True)
    except subprocess.CalledProcessError:
        pass


def triage(content: str, source: str) -> dict | None:
    if not OVERSEER_API_URL:
        return None
    try:
        r = httpx.post(
            f"{OVERSEER_API_URL}/triage",
            json={"content": content[:3000], "source": source},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[tagger] triage error: {e}", file=sys.stderr)
        return None


def process_file(path: Path) -> None:
    content = path.read_text()
    source = "yap" if "yap" in str(path) else "inbox"
    print(f"[tagger] processing {path.name} ({source})")

    result = triage(content, source)
    if not result:
        return

    dest = result.get("destination", "")
    summary = result.get("summary", "")
    tags = result.get("tags", [])
    entities = result.get("entities", {})

    if not dest or dest == "ignore":
        return

    today = datetime.now().strftime("%Y-%m")
    wiki_file = VAULT_PATH / dest / f"digest-{today}.md"
    wiki_file.parent.mkdir(parents=True, exist_ok=True)

    entry_lines = [
        f"\n### {datetime.now().strftime('%Y-%m-%d %H:%M')} — {summary}",
        f"Tags: {', '.join(tags)}",
        f"Source: [[{path.relative_to(VAULT_PATH)}]]",
    ]

    people = entities.get("people", [])
    if people:
        entry_lines.append(f"People: {', '.join(f'[[wiki/personal/people/{p}|{p}]]' for p in people)}")

    entry_lines.append("")

    with wiki_file.open("a") as f:
        f.write("\n".join(entry_lines) + "\n")

    vault_commit(wiki_file, f"tagger: digest {source} → {dest}")

    # Create/update person notes for any new people
    for person in people:
        person_file = VAULT_PATH / "wiki" / "personal" / "people" / f"{person}.md"
        if not person_file.exists():
            today_str = datetime.now().strftime("%Y-%m-%d")
            person_file.write_text(
                f"---\ntitle: {person}\npartition: personal\ntype: person\n"
                f"name: {person}\nrelationship: \nbirthday: \n"
                f"tags: [people, personal]\nsources: [synthesized]\n"
                f"created: {today_str}\nupdated: {today_str}\n---\n\n"
                f"# {person}\n\nPart of [[personal/MOC|Personal]].\n\n## Facts\n\n## Notes\n"
            )
            vault_commit(person_file, f"tagger: new person note — {person}")


def scan_inbox() -> None:
    seen = load_seen()
    inbox = VAULT_PATH / "inbox"

    new_files: list[Path] = []
    for path in sorted(inbox.rglob("*.md")):
        key = str(path.relative_to(VAULT_PATH))
        if key not in seen:
            # skip gitkeep stubs and the raw day-files (only process yap/)
            if path.stat().st_size > 50:
                new_files.append(path)
            seen.add(key)

    for path in new_files:
        try:
            process_file(path)
        except Exception as e:
            print(f"[tagger] error processing {path}: {e}", file=sys.stderr)

    save_seen(seen)


def main() -> None:
    if not OVERSEER_API_URL:
        print("[tagger] OVERSEER_API_URL not set — triage disabled, exiting", file=sys.stderr)
        sys.exit(1)

    print(f"[tagger] watching {VAULT_PATH}/inbox/ every {POLL_INTERVAL}s")
    while True:
        try:
            scan_inbox()
        except Exception as e:
            print(f"[tagger] scan error: {e}", file=sys.stderr)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
