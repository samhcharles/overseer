#!/usr/bin/env python3
"""
novel_pattern_detector.py — scans ~/vault/wiki/inbox-novel/ for recurring
patterns in quarantined items. When ≥3 items share an extracted entity or topic,
emits a cluster-genesis proposal Overseer surfaces at next session.

Run on a daily cron. Reads frontmatter `extracted_entities` from each capture.

Outputs:
  ~/vault/wiki/inbox-novel/_proposals.md  — current proposals (overwritten each run)

Anti-runaway:
  - Rejected proposals decay: if Sam rejects a proposal, add the entity to a
    skip list (~/.local/state/overseer/proposal-skips.json) and don't refire
    for N days.
"""
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

VAULT = Path(os.environ.get("VAULT_PATH", Path.home() / "vault"))
NOVEL_DIR = VAULT / "wiki" / "inbox-novel"
PROPOSALS_FILE = NOVEL_DIR / "_proposals.md"
SKIP_FILE = Path.home() / ".local" / "state" / "overseer" / "proposal-skips.json"
MIN_ITEMS_TO_PROPOSE = 3
SKIP_DAYS = 14


def load_skips() -> dict[str, str]:
    if not SKIP_FILE.exists():
        return {}
    try:
        return json.loads(SKIP_FILE.read_text())
    except Exception:
        return {}


def skip_expired(skips: dict[str, str]) -> dict[str, str]:
    cutoff = datetime.now() - timedelta(days=SKIP_DAYS)
    return {k: v for k, v in skips.items() if datetime.fromisoformat(v) > cutoff}


def parse_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    fm: dict[str, object] = {}
    for line in text[4:end].splitlines():
        m = re.match(r"^([a-zA-Z_]+):\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val.startswith("[") and val.endswith("]"):
            fm[key] = [v.strip().strip('"') for v in val[1:-1].split(",") if v.strip()]
        else:
            fm[key] = val.strip('"')
    return fm


def scan_novel() -> list[tuple[str, list[str]]]:
    if not NOVEL_DIR.exists():
        return []
    out: list[tuple[str, list[str]]] = []
    for f in NOVEL_DIR.glob("*.md"):
        if f.name.startswith("_") or f.stem == "index":
            continue
        try:
            fm = parse_frontmatter(f.read_text())
        except Exception:
            continue
        if fm.get("triaged") in (True, "true"):
            continue
        entities = fm.get("extracted_entities", [])
        if isinstance(entities, str):
            entities = [entities]
        if entities:
            out.append((str(f.relative_to(VAULT)), entities))
    return out


def find_clusters(items: list[tuple[str, list[str]]], skips: dict[str, str]) -> dict[str, list[str]]:
    """Group items by entity. Return {entity: [paths]} for entities with ≥ MIN_ITEMS_TO_PROPOSE matches."""
    by_entity: dict[str, list[str]] = {}
    for path, entities in items:
        for ent in entities:
            ent = ent.lower().strip()
            if not ent or ent in skips:
                continue
            by_entity.setdefault(ent, []).append(path)
    return {k: v for k, v in by_entity.items() if len(v) >= MIN_ITEMS_TO_PROPOSE}


def write_proposals(clusters: dict[str, list[str]]) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    NOVEL_DIR.mkdir(parents=True, exist_ok=True)
    if not clusters:
        PROPOSALS_FILE.write_text(
            f"---\ntitle: Novel cluster proposals\npartition: inbox-novel\ntype: proposal-set\n"
            f"updated: {today}\nsources: [novel_pattern_detector]\ntags: [inbox, proposals]\n---\n\n"
            f"# Proposals\n\nNo patterns yet. Add more captures to inbox-novel/ to see proposals.\n"
        )
        return
    lines = [
        "---",
        "title: Novel cluster proposals",
        "partition: inbox-novel",
        "type: proposal-set",
        f"updated: {today}",
        "sources: [novel_pattern_detector]",
        "tags: [inbox, proposals]",
        "---",
        "",
        "# Proposals",
        "",
        f"Overseer will surface these in the next session and ask before creating any new partition.",
        "",
    ]
    for entity, paths in sorted(clusters.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"## `{entity}` ({len(paths)} items)")
        lines.append("")
        for p in paths:
            lines.append(f"- [[{p}]]")
        lines.append("")
        lines.append(f"**Proposed partition:** `wiki/{entity}/` (Overseer will design schema + dashboard on accept)")
        lines.append("")
    PROPOSALS_FILE.write_text("\n".join(lines))


def main() -> int:
    skips = skip_expired(load_skips())
    items = scan_novel()
    clusters = find_clusters(items, skips)
    write_proposals(clusters)
    print(f"[novel-detector] scanned {len(items)} captures, found {len(clusters)} cluster proposals", file=sys.stderr)
    for entity, paths in clusters.items():
        print(f"  · {entity} ({len(paths)} items)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
