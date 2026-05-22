#!/usr/bin/env python3
"""
linker.py — daily cron job. Finds wiki notes that mention names or concepts
from other notes but don't have [[backlinks]]. Asks Overseer to add them.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx

from vault_utils import vault_write_atomic

VAULT_PATH = Path(os.environ.get("VAULT_PATH", Path.home() / "vault"))
OVERSEER_API_URL = os.environ.get("OVERSEER_API_URL", "")


def all_wiki_titles() -> dict[str, Path]:
    """Returns {title: path} for all wiki notes."""
    titles: dict[str, Path] = {}
    for p in (VAULT_PATH / "wiki").rglob("*.md"):
        title = p.stem.replace("-", " ").replace("_", " ").lower()
        titles[title] = p
        # also index by frontmatter title if present
        try:
            content = p.read_text()
            m = re.search(r'^title:\s*(.+)$', content, re.MULTILINE)
            if m:
                titles[m.group(1).strip().lower()] = p
        except Exception:
            pass
    return titles


def find_orphan_mentions(note_path: Path, titles: dict[str, Path]) -> list[str]:
    """Find titles mentioned in note text but not linked with [[...]]."""
    try:
        content = note_path.read_text()
    except Exception:
        return []

    existing_links = set(re.findall(r'\[\[([^\]]+)\]\]', content))
    orphans: list[str] = []

    for title, path in titles.items():
        if path == note_path:
            continue
        # skip if already linked
        if any(title.lower() in link.lower() for link in existing_links):
            continue
        # check if title text appears in content (case-insensitive, word boundary)
        if re.search(r'\b' + re.escape(title) + r'\b', content, re.IGNORECASE):
            orphans.append(path.stem)

    return orphans[:5]  # limit to 5 per note to avoid spam


def vault_commit(path: Path, msg: str) -> None:
    try:
        subprocess.run(["git", "-C", str(VAULT_PATH), "add", str(path)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(VAULT_PATH), "commit", "-m", msg], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(VAULT_PATH), "push", "origin", "main"], capture_output=True)
    except subprocess.CalledProcessError:
        pass


def add_backlinks(note_path: Path, orphans: list[str]) -> None:
    """Add a See Also section with backlinks if it doesn't exist."""
    content = note_path.read_text()

    # Don't add if already has Related or See Also section
    if re.search(r'^## (Related|See [Aa]lso)', content, re.MULTILINE):
        # Just append the links to existing section
        links = " ".join(f"[[{o}]]" for o in orphans)
        new_content = re.sub(
            r'(^## (Related|See [Aa]lso)\n)',
            f'\\1{links}\n',
            content, count=1, flags=re.MULTILINE
        )
    else:
        links = " ".join(f"[[{o}]]" for o in orphans)
        new_content = content.rstrip() + f"\n\n## Related\n\n{links}\n"

    vault_write_atomic(note_path, new_content)
    vault_commit(note_path, f"linker: add backlinks to {note_path.stem}")
    print(f"[linker] {note_path.stem} ← {orphans}")


def main() -> None:
    print("[linker] scanning wiki for orphan mentions...")
    titles = all_wiki_titles()
    linked = 0

    for note in sorted((VAULT_PATH / "wiki").rglob("*.md")):
        if note.name.startswith("_") or note.name.startswith("MOC"):
            continue
        orphans = find_orphan_mentions(note, titles)
        if orphans:
            add_backlinks(note, orphans)
            linked += 1

    print(f"[linker] done — updated {linked} notes")


if __name__ == "__main__":
    main()
