# Overseer

Personal AI operating system. Terminal-first, vault-connected, self-hosted.

```
   ╭──────────╮
 ╭─╯          ╰─╮
 │██╭────────╮██│
 │██│  ██▌██  │██│
 │██╰────────╯██│
 ╰─╮          ╭─╯
   ╰──────────╯
```

## What it is

A TUI that connects to a self-hosted AI gateway. The gateway rotates across free/local inference providers (Groq, Gemini, OpenRouter, Ollama) and has read/write access to a local Markdown vault. Three modes control what the AI can do: read-only chat, private reasoning, and write-enabled capture.

It is not a wrapper around a single API. It is not a SaaS. It runs on your machine and your VPS.

## Stack

| Layer | Tech |
|---|---|
| TUI | Ink 5, React 18, Node.js |
| API gateway | FastAPI, Python 3.12 |
| Edge helpers | Cloudflare Workers |
| Background agents | Python, systemd |
| Storage | Local Markdown vault, git-backed |

## Quickstart

### TUI

```bash
cd tui
npm install
npm run build
node dist/overseer.mjs
```

To install the `overseer` command globally:

```bash
echo '#!/usr/bin/env bash' > ~/.local/bin/overseer
echo 'exec node ~/path/to/overseer/tui/dist/overseer.mjs "$@"' >> ~/.local/bin/overseer
chmod +x ~/.local/bin/overseer
```

### API gateway

```bash
cd api
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8765
```

Set at least one provider key:

```bash
export GROQ_API_KEY=...
export GEMINI_API_KEY=...
export OPENROUTER_API_KEY=...
```

## Configuration

The TUI reads `OVERSEER_API_URL` and `OVERSEER_API_KEY` from `~/.secrets/master.env` or the environment. All other config lives in a `runtime-config.json` file managed by the `/providers` command inside the TUI.

| Variable | Default | Purpose |
|---|---|---|
| `OVERSEER_API_URL` | `http://127.0.0.1:8765` | Gateway URL |
| `OVERSEER_API_KEY` | — | Auth key |
| `GROQ_API_KEY` | — | Groq inference |
| `GEMINI_API_KEY` | — | Gemini inference |
| `OPENROUTER_API_KEY` | — | OpenRouter inference |
| `OLLAMA_URL` | `http://localhost:11434` | Local Ollama |
| `VAULT_PATH` | `~/vault` | Markdown vault root |

## Modes

Switch with `Shift+Tab`.

| Mode | Indicator | Write access |
|---|---|---|
| chat | `[o]` | No |
| think | `[!]` | No |
| capture | `[+]` | Yes |

`chat` and `think` are strictly read-only. The AI cannot create or modify vault files in those modes. `capture` enables entity extraction and structured vault writes.

## Provider rotation

The gateway tries providers in round-robin order. When one returns a rate limit error it is blocked for a cooldown period and the next slot is tried. Providers are configured at runtime via `/providers` in the TUI — no restart needed.

Default rotation order: OpenRouter free models → Gemini → Groq → Ollama.

Local Ollama and trusted Tailscale nodes are preferred when available.

## Key bindings

| Key | Action |
|---|---|
| `Enter` | Send message |
| `Alt+Enter` | New line |
| `Ctrl+J` | New line (universal fallback) |
| `Shift+Tab` | Cycle mode |
| `/` | Command picker |
| `?` | Show key bindings |
| `Esc` | Stop running request |
| `Ctrl+C × 2` | Exit |

> Note: `Shift+Enter` works only in terminals that support the kitty keyboard protocol (Windows Terminal with kitty mode, kitty, WezTerm). Overseer requests kitty mode on startup — if your terminal supports it, `Shift+Enter` will work automatically.

## Commands

Type `/` to open the command picker. Available commands:

```
/chat        main chat view
/vault       browse vault files
/sessions    session history
/history     recent message history
/usage       token usage and runtime info
/providers   manage providers and models
/health      gateway status
/model       active runtime
/help        show commands
/quit        exit
```

## Architecture

```
overseer (TUI)
    │  HTTP
    ▼
api/main.py
    ├── provider rotator (Groq, Gemini, OpenRouter, Ollama)
    ├── vault tools (read, write, search)
    ├── node registry (Tailscale inference nodes)
    └── mode enforcement (chat/think = read-only, capture = write)
         │
         ▼
vault/  (git-backed Markdown)
    └── agents/ (tagger, linker, yap_processor — systemd services)
```

## Background agents

Three systemd services watch the vault inbox and process raw notes:

- **tagger** — assigns tags to new vault files
- **linker** — creates wiki-links between related notes
- **yap_processor** — processes raw text dumps, extracts entities, routes to the correct vault partition

These are optional. The TUI and API work without them.

## License

MIT
