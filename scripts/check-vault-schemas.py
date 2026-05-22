#!/usr/bin/env python3
"""
check-vault-schemas.py — single-pass frontmatter audit for ~/vault/wiki/.

Checks every wiki .md file for required YAML frontmatter keys, validates
the partition enum, and flags synthesized sources as warnings.

Exit codes:
  0 — clean (or warnings only)
  1 — one or more required fields missing or invalid

Usage:
  python3 check-vault-schemas.py [--vault VAULT_PATH] [--output]
"""
import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

REQUIRED_KEYS = {"title", "partition", "sources", "created", "updated"}
VALID_PARTITIONS = {"orinadus", "madhouse", "personal", "knowledge", "systems"}


def parse_frontmatter(text: str) -> dict | None:
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    end = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        return None

    fm: dict = {}
    i = 1
    while i < end:
        line = lines[i]
        m = re.match(r'^([\w][\w_-]*):\s*(.*)', line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if not val:
                items: list[str] = []
                j = i + 1
                while j < end and re.match(r'^\s*-\s', lines[j]):
                    items.append(lines[j].strip().lstrip("- ").strip())
                    j += 1
                fm[key] = items
                i = j
                continue
            fm[key] = val
        i += 1
    return fm


def audit_note(path: Path, vault_root: Path) -> tuple[list[str], list[str]]:
    """Returns (errors, warnings) for a single note."""
    errors: list[str] = []
    warnings: list[str] = []
    rel = path.relative_to(vault_root)

    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        errors.append(f"  {rel}: cannot read — {e}")
        return errors, warnings

    fm = parse_frontmatter(text)
    if fm is None:
        errors.append(f"  {rel}: missing frontmatter block")
        return errors, warnings

    missing = REQUIRED_KEYS - fm.keys()
    for key in sorted(missing):
        errors.append(f"  {rel}: missing required key '{key}'")

    partition = fm.get("partition", "")
    if partition and partition not in VALID_PARTITIONS:
        errors.append(f"  {rel}: invalid partition '{partition}' (valid: {sorted(VALID_PARTITIONS)})")

    sources = fm.get("sources", [])
    if isinstance(sources, list) and sources == ["synthesized"]:
        warnings.append(f"  {rel}: sources: [synthesized] — no raw/ trace")
    elif isinstance(sources, str) and sources == "synthesized":
        warnings.append(f"  {rel}: sources: synthesized — no raw/ trace")

    return errors, warnings


def write_health_report(vault_root: Path, all_errors: list[str], all_warnings: list[str], total: int) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    outputs_dir = vault_root / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    report_path = outputs_dir / f"health-{today}.md"

    lines = [
        f"# Vault Health: {today}\n",
        f"Scanned: {total} wiki notes\n",
        f"Errors: {len(all_errors)} | Warnings: {len(all_warnings)}\n",
    ]
    if all_errors:
        lines.append("\n## Errors\n")
        lines.extend(e + "\n" for e in all_errors)
    if all_warnings:
        lines.append("\n## Warnings\n")
        lines.extend(w + "\n" for w in all_warnings)
    if not all_errors and not all_warnings:
        lines.append("\nAll notes pass schema validation.\n")

    report_path.write_text("".join(lines), encoding="utf-8")
    print(f"report written: {report_path}")

    log_path = vault_root / "wiki" / "_log.md"
    if log_path.exists():
        entry = (
            f"\n## [{today}] lint | vault schema check | "
            f"{len(all_errors)} error(s), {len(all_warnings)} warning(s), {total} notes scanned\n"
        )
        with log_path.open("a", encoding="utf-8") as f:
            f.write(entry)


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit vault wiki frontmatter schemas")
    parser.add_argument("--vault", default=os.environ.get("VAULT_PATH", str(Path.home() / "vault")),
                        help="Path to vault root")
    parser.add_argument("--output", action="store_true",
                        help="Write health report to outputs/ and append to _log.md")
    args = parser.parse_args()

    vault_root = Path(args.vault)
    wiki_dir = vault_root / "wiki"

    if not wiki_dir.exists():
        print(f"error: wiki directory not found at {wiki_dir}", file=sys.stderr)
        sys.exit(1)

    all_errors: list[str] = []
    all_warnings: list[str] = []
    total = 0

    for note in sorted(wiki_dir.rglob("*.md")):
        total += 1
        errs, warns = audit_note(note, vault_root)
        all_errors.extend(errs)
        all_warnings.extend(warns)

    print(f"scanned {total} notes — {len(all_errors)} error(s), {len(all_warnings)} warning(s)")

    if all_errors:
        print("\nERRORS:")
        for e in all_errors:
            print(e)

    if all_warnings:
        print("\nWARNINGS:")
        for w in all_warnings:
            print(w)

    if args.output:
        write_health_report(vault_root, all_errors, all_warnings, total)

    sys.exit(1 if all_errors else 0)


if __name__ == "__main__":
    main()
