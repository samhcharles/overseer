# Overseer

A local-first AI that lives on your machine, reads and writes a structured vault, and grows with you. Built for one person, shared so others can adopt it.

## Quickstart

```bash
# 1. Install Ollama and pull a model
curl -fsSL https://ollama.com/install.sh | sh
ollama pull dolphin3

# 2. Clone and scaffold the vault
git clone https://github.com/samhcharles/overseer.git
cd overseer
./setup.sh

# 3. Start the API node
cd api && pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8765 &

# 4. Install and run the TUI
npm install -g @samhcharles/overseer
overseer
```

`setup.sh` asks for your vault path, scaffolds partitions and dashboards, initialises the vault as a git repo, and lets you choose your LLM provider (Ollama, Gemini, or OpenRouter).

If you prefer to build the TUI from source: `cd tui-go && ./build.sh --install`.

## How it works

Three pieces:

- **`api/`** — FastAPI node. Reads and writes your vault, runs tool calls, streams responses. Runs locally; your vault never leaves your machine.
- **`tui-go/`** — Bubble Tea terminal client. Single Go binary, no runtime dependencies.
- **`~/vault`** — Your vault. The source of truth. Overseer never invents facts; every personal claim is tagged `[vault: <path>]`, `[inferred]`, or `[unknown]`.

The vault starts with typed partitions (sales, bookmarks, reading, journal, contacts, travel, projects, and more). When you mention something that doesn't fit any partition, Overseer parks it in `inbox-novel/`. Once similar items accumulate, it proposes a new partition with schema and dashboard.

## Environment

| var | default | notes |
|---|---|---|
| `OVERSEER_API_URL` | `http://localhost:8765` | API address the TUI connects to |
| `VAULT_PATH` | set by setup.sh | absolute path to your vault |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama endpoint |
| `OLLAMA_MODEL` | `dolphin3` | model name |

## Key bindings

| key | action |
|---|---|
| `enter` | send |
| `alt+enter` / `ctrl+j` | newline |
| `shift+enter` | newline (on terminals with modify-other-keys) |
| `ctrl+g` | open `$EDITOR` for the input buffer |
| `esc` | cancel in-flight stream |
| `↑` / `↓` | cycle input history (empty input) or scroll |
| `pgup` / `pgdn` | scroll messages |
| `ctrl+n` | new thread |
| `ctrl+l` | session list |
| `ctrl+y` | copy last response to clipboard |
| `?` | help |
| `ctrl+c` | quit |

## Slash commands

| command | description |
|---|---|
| `/new` | start a new thread |
| `/sessions` | open the session list |
| `/clear` | clear the current chat display |
| `/activity` | show tool calls from this session |
| `/journal` | write today's journal entry |
| `/idea` | quick-capture an idea |
| `/bookmark` | save a URL |
| `/deal` | add a sales deal |
| `/model` | show or switch the active Ollama model |
| `/theme` | switch colour theme (dark, light, term) |
| `/help` | full key and command reference |
| `/quit` | exit |

## Repo layout

| dir | purpose |
|---|---|
| `api/` | FastAPI Overseer node |
| `tui-go/` | Bubble Tea TUI (Go) |
| `gateway/` | Always-on VPS router (optional) |
| `agents/` | Background brain agents (tagger, linker, yap_processor) |
| `scripts/` | Utilities including novel-pattern detector |
| `templates/` | Vault scaffolds for new installs |

## License

MIT — see [LICENSE](LICENSE).
