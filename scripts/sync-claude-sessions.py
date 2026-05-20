#!/usr/bin/env python3
"""
sync-claude-sessions.py — converts Claude Code JSONL sessions to raw vault markdown.

Reads all .jsonl files from ~/.claude/projects/-home-samhc/,
skips sessions already listed in vault/raw/sessions/.processed,
writes full-fidelity markdown to vault/raw/sessions/,
then commits and pushes the batch.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

VAULT_PATH = Path(os.environ.get("VAULT_PATH", Path.home() / "vault"))
CLAUDE_PROJECTS_DIR = Path.home() / ".claude/projects/-home-samhc"
RAW_SESSIONS_DIR = VAULT_PATH / "raw" / "sessions"
MANIFEST_PATH = RAW_SESSIONS_DIR / ".processed"

TOOL_RESULT_CAP = 3000


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def load_manifest() -> set[str]:
    if not MANIFEST_PATH.exists():
        return set()
    return {line.strip() for line in MANIFEST_PATH.read_text().splitlines() if line.strip()}


def append_manifest(session_ids: list[str]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST_PATH.open("a") as f:
        for sid in session_ids:
            f.write(sid + "\n")


# ---------------------------------------------------------------------------
# JSONL parsing
# ---------------------------------------------------------------------------

def extract_session_id(lines: list[dict]) -> str | None:
    """Return the sessionId from the first permission-mode record or any record."""
    for obj in lines:
        if obj.get("type") == "permission-mode" and obj.get("sessionId"):
            return obj["sessionId"]
    # Fallback: any record with sessionId
    for obj in lines:
        if obj.get("sessionId"):
            return obj["sessionId"]
    return None


def extract_first_timestamp(lines: list[dict]) -> str:
    """Return the ISO timestamp from the first user message, or now."""
    for obj in lines:
        if obj.get("type") == "user" and obj.get("timestamp"):
            ts = obj["timestamp"]
            # Normalise to Z suffix
            if ts.endswith("+00:00"):
                ts = ts[:-6] + "Z"
            return ts
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def render_tool_result(content) -> str:
    """Render the content of a tool-result record."""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(item.get("text", str(item)))
            else:
                parts.append(str(item))
        text = "\n".join(parts)
    else:
        text = str(content)

    truncated = False
    if len(text) > TOOL_RESULT_CAP:
        text = text[:TOOL_RESULT_CAP]
        truncated = True

    result = f"```\n{text}"
    if truncated:
        result += "\n[...truncated at 3000 chars]"
    result += "\n```"
    return result


def render_assistant_content(content_blocks: list[dict]) -> str:
    """Render the content blocks of an assistant message."""
    parts = []
    for block in content_blocks:
        btype = block.get("type")
        if btype == "text":
            text = block.get("text", "").strip()
            if text:
                parts.append(text)
        elif btype == "thinking":
            thinking = block.get("thinking", "").strip()
            if thinking:
                parts.append(f"**Thinking:**\n{thinking}")
        elif btype == "tool_use":
            name = block.get("name", "unknown")
            inp = block.get("input", {})
            lines = []
            if "description" in inp:
                lines.append(f"description: {inp['description']}")
            if "command" in inp:
                lines.append(f"command: {inp['command']}")
            # For non-Bash tools, dump all input keys
            if not lines:
                for k, v in inp.items():
                    v_str = str(v)
                    if len(v_str) > 500:
                        v_str = v_str[:500] + "..."
                    lines.append(f"{k}: {v_str}")
            inner = "\n".join(lines)
            parts.append(f"**Tool: {name}**\n```\n{inner}\n```")
    return "\n\n".join(parts)


def build_markdown(lines: list[dict], session_id: str, captured_at: str, source_path: str) -> str:
    """Build full-fidelity markdown from parsed JSONL lines."""
    date_str = captured_at[:10]
    sections: list[str] = []

    # Track tool names by tool_use_id so we can label tool results
    tool_id_to_name: dict[str, str] = {}

    for obj in lines:
        msg_type = obj.get("type")

        if msg_type == "user":
            content = obj.get("message", {}).get("content", "")
            if isinstance(content, str):
                # Plain human message
                if content.strip():
                    sections.append(f"### Human\n\n{content.strip()}\n\n---")
            elif isinstance(content, list):
                # May contain tool_result blocks
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        tool_id = item.get("tool_use_id", "")
                        tool_name = tool_id_to_name.get(tool_id, "unknown")
                        result_content = item.get("content", "")
                        rendered = render_tool_result(result_content)
                        sections.append(f"### Tool result ({tool_name})\n\n{rendered}\n\n---")

        elif msg_type == "assistant":
            content_blocks = obj.get("message", {}).get("content", [])
            if not isinstance(content_blocks, list):
                continue
            # Index tool_use ids
            for block in content_blocks:
                if block.get("type") == "tool_use":
                    tid = block.get("id", "")
                    tname = block.get("name", "unknown")
                    if tid:
                        tool_id_to_name[tid] = tname

            rendered = render_assistant_content(content_blocks)
            if rendered.strip():
                sections.append(f"### Assistant\n\n{rendered}\n\n---")

    body = "\n\n".join(sections)
    word_count = len(body.split())

    frontmatter = (
        "---\n"
        f"tool: claude\n"
        f"session_id: {session_id}\n"
        f"captured_at: {captured_at}\n"
        f"processed: false\n"
        f"source_path: {source_path}\n"
        f"word_count: {word_count}\n"
        "---"
    )

    return f"{frontmatter}\n\n# Claude Session — {date_str}\n\n---\n\n{body}\n"


# ---------------------------------------------------------------------------
# Main sync logic
# ---------------------------------------------------------------------------

def sync_session(jsonl_path: Path, manifest: set[str]) -> str | None:
    """
    Parse a single JSONL file and write its markdown.
    Returns the session_id on success, None if skipped/failed.
    """
    try:
        raw_lines = jsonl_path.read_text().splitlines()
    except OSError as e:
        print(f"[sync] warning: cannot read {jsonl_path.name}: {e}", file=sys.stderr)
        return None

    parsed: list[dict] = []
    for i, line in enumerate(raw_lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError as e:
            print(f"[sync] warning: {jsonl_path.name} line {i} malformed JSON: {e}", file=sys.stderr)

    if not parsed:
        print(f"[sync] warning: {jsonl_path.name} has no valid JSON lines — skipping", file=sys.stderr)
        return None

    session_id = extract_session_id(parsed)
    if not session_id:
        print(f"[sync] warning: {jsonl_path.name} has no session_id — skipping", file=sys.stderr)
        return None

    if session_id in manifest:
        return None  # already processed, silently skip

    captured_at = extract_first_timestamp(parsed)
    date_str = captured_at[:10]
    short_id = session_id.replace("-", "")[:8]
    out_filename = f"{date_str}-claude-{short_id}.md"
    out_path = RAW_SESSIONS_DIR / out_filename

    source_path = str(jsonl_path)
    md = build_markdown(parsed, session_id, captured_at, source_path)

    RAW_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md)
    print(f"[sync] wrote {out_filename}")
    return session_id


def git_commit_batch(new_session_ids: list[str]) -> None:
    n = len(new_session_ids)
    try:
        subprocess.run(
            ["git", "-C", str(VAULT_PATH), "add", "raw/sessions/"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(VAULT_PATH), "commit", "-m", f"sync: {n} claude session{'s' if n != 1 else ''} → raw/sessions/"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(VAULT_PATH), "push", "origin", "main"],
            capture_output=True,
        )
        print(f"[sync] committed and pushed {n} session{'s' if n != 1 else ''}")
    except subprocess.CalledProcessError as e:
        print(f"[sync] git error: {e}", file=sys.stderr)


def main() -> None:
    jsonl_files = sorted(CLAUDE_PROJECTS_DIR.glob("*.jsonl"))
    if not jsonl_files:
        print("[sync] no .jsonl files found in", CLAUDE_PROJECTS_DIR)
        return

    manifest = load_manifest()
    new_ids: list[str] = []
    skipped = 0

    for jf in jsonl_files:
        # Quick pre-check: derive session_id from filename stem if it looks like a UUID
        stem = jf.stem
        if stem in manifest:
            skipped += 1
            continue

        result = sync_session(jf, manifest)
        if result is None:
            skipped += 1
        else:
            new_ids.append(result)

    if new_ids:
        append_manifest(new_ids)
        git_commit_batch(new_ids)

    total = len(new_ids)
    print(f"[sync] synced {total} session{'s' if total != 1 else ''}, skipped {skipped} already processed")


if __name__ == "__main__":
    main()
