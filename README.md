# Overseer

A local-first AI that lives on your machine, talks to a structured Obsidian vault, and grows with you. Built for one person, shared so others can adopt it.

## Quickstart

```bash
# 1. Install Ollama and pull a model
curl -fsSL https://ollama.com/install.sh | sh
ollama pull dolphin3

# 2. Clone and scaffold
git clone https://github.com/samhcharles/overseer.git ~/dev/overseer
cd ~/dev/overseer
./setup.sh

# 3. Start the API node
cd api && pip install -r requirements.txt && ./start.sh &

# 4. Build and launch the TUI
cd ../tui-go && ./build.sh --install
overseer
```

`setup.sh` asks for your vault path, scaffolds ~18 partition folders + dashboards + Obsidian plugin config, initialises the vault as a git repo, and lets you pick your LLM provider (Ollama, Gemini, or OpenRouter).

## How it works

Three pieces:

- **`api/`** — FastAPI node. Reads and writes your vault, runs tool calls, streams responses.
- **`tui-go/`** — Bubble Tea terminal client. Single Go binary.
- **`~/vault`** — Your Obsidian vault. The source of truth. Overseer never invents facts; every personal claim is tagged `[vault: <path>]`, `[inferred]`, or `[unknown]`.

The vault starts with ~18 typed partitions (sales, bookmarks, reading, journal, contacts, travel, projects, …). When you mention something that doesn't fit any partition, Overseer parks it in `inbox-novel/`. Once three similar items accumulate, it proposes creating a new partition with schema and dashboard — the brain grows new lobes.

## Key bindings

| key | action |
|---|---|
| `enter` | send |
| `alt+enter` | newline |
| `/` | open command palette |
| `esc` | cancel in-flight stream |
| `↑` | cycle input history / scroll |
| `ctrl+n` | new thread |
| `ctrl+l` | session list |
| `ctrl+c` | quit |

## Slash commands

`/new` `/sessions` `/clear` `/activity` `/journal` `/idea` `/bookmark` `/deal` `/help` `/quit`

## Config

See [`.env.example`](.env.example). Required: `VAULT_PATH`, `OLLAMA_URL`, `OLLAMA_MODEL`.

## Repo layout

| dir | purpose |
|---|---|
| `api/` | FastAPI Overseer node |
| `tui-go/` | Bubble Tea TUI (Go) |
| `gateway/` | Always-on VPS router (optional) |
| `agents/` | Background brain agents |
| `scripts/` | Utilities including novel-pattern detector |
| `templates/` | Vault scaffolds for new installs |

## License

MIT — see [LICENSE](LICENSE).
