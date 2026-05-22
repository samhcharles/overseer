# Overseer

> *"JARVIS, run a diagnostic."*
>
> Overseer is my personal AI operating system. Like JARVIS is for Tony Stark, Overseer is always on, always watching, and knows everything I need it to know. Right now it lives in software. One day it will be hardware. Eventually it will be me.

```
   ╭──────────╮
 ╭─╯          ╰─╮
 │██╭────────╮██│
 │██│  ██▌██  │██│
 │██╰────────╯██│
 ╰─╮          ╭─╯
   ╰──────────╯

I am Overseer.
Ek sé allt.
```

## What it does

- **Terminal TUI** — type `overseer` and get a full chat interface with an animated eye
- **Vault-connected** — reads and writes to a personal knowledge vault
- **Provider rotation** — cycles across Groq, Gemini, OpenRouter, Ollama automatically, rotating when one rate-limits
- **Three modes** — `chat` (read-only), `think` (private reasoning), `capture` (writes durable notes)
- **Local node support** — trusted devices on Tailscale can serve as inference nodes
- **Background agents** — tagger, linker, and yap_processor watch the inbox and process raw notes

## Stack

| Layer | Tech |
|---|---|
| TUI | Ink 5 + React 18 (Node) |
| API | FastAPI + Python 3.12 |
| Edge helpers | Cloudflare Workers |
| Background agents | Python systemd services |
| Storage | Personal Obsidian vault (git-backed) |

## Getting started

### TUI

```bash
cd tui
npm install
npm run build
node dist/overseer.mjs
```

Add to PATH:

```bash
echo 'alias overseer="node ~/path/to/overseer/tui/dist/overseer.mjs"' >> ~/.bashrc
```

The TUI reads `OVERSEER_API_URL` and `OVERSEER_API_KEY` from `~/.secrets/master.env` or environment.

### API

```bash
cd api
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8765
```

Set at minimum one provider key: `GROQ_API_KEY`, `GEMINI_API_KEY`, or `OPENROUTER_API_KEY`.

Optional vault integration: set `VAULT_PATH` to a local Obsidian/markdown directory.

### Environment variables

| Variable | Purpose |
|---|---|
| `OVERSEER_API_URL` | Gateway URL (default: `http://127.0.0.1:8765`) |
| `OVERSEER_API_KEY` | Auth key for the API |
| `GROQ_API_KEY` | Groq inference |
| `GEMINI_API_KEY` | Gemini inference |
| `OPENROUTER_API_KEY` | OpenRouter inference |
| `OLLAMA_URL` | Local Ollama server |
| `VAULT_PATH` | Path to personal vault directory |

## Architecture

```
overseer (terminal)
    │
    ▼
api/main.py  ←── provider rotation (Groq, Gemini, OpenRouter, Ollama)
    │
    ▼
vault/  ←── git-backed markdown knowledge base
    │
    ▼
agents/  ←── background processors (tagger, linker, yap_processor)
```

## Modes

| Mode | Key | Behavior |
|---|---|---|
| chat | `[o]` | Read-only conversation and retrieval |
| think | `[!]` | Private reasoning — nothing saved |
| capture | `[+]` | Write-enabled — can extract and store notes |

Switch with `Shift+Tab`.

## Vision

Overseer is phase one of a longer arc. Software first, hardware eventually. The goal is a system that knows my context the way I do: work, relationships, projects, history. No chat window, no web app, no SaaS subscription. Just a terminal, a vault, and an eye that watches.

---

Built by [Sam Charles](https://github.com/samhcharles).
