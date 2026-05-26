# Overseer

Your personal Jarvis. A local-first AI that lives on your machine, talks to a structured Obsidian vault (your second brain), and grows along with you. Built originally for one person, shared so others can adopt it.

> **Not affiliated with Mad House or Orinadus.** Overseer is a personal tool. It happens to integrate with [Urchin](https://github.com/orinadus-systems/urchin) and [Sleipnir](https://github.com/orinadus-systems/sleipnir) — both standalone Orinadus products — but neither is required.

## What this is

```
┌─ Ollama (local model) ──────────────┐
│         dolphin3 / qwen / llama     │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│ api/         FastAPI node           │
│              reads + writes ~/vault │
│              text-based tool calls  │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│ tui-go/      Bubble Tea TUI         │
│              single Go binary       │
└─────────────────────────────────────┘

┌─ ~/vault (your second brain) ───────┐
│  wiki/                              │
│    sales/  bookmarks/  reading/     │
│    journal/  projects/  contacts/   │
│    travel/  inbox-ideas/            │
│    inbox-novel/  ← grows new        │
│    + 9 canonical partitions         │
│  dashboards/                        │
│    sales · journal · contacts · …   │
│  ~ 18 visual surfaces in Obsidian   │
└─────────────────────────────────────┘
```

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

`setup.sh` will:
- Ask for your vault path (default `~/vault`)
- Scaffold ~17 partition folders + dashboards + Obsidian community plugin config
- Initialize the vault as a git repo
- Ask which LLM provider you want as default (Ollama / Gemini / OpenRouter)
- Write `.env` and verify Ollama is reachable

## How Overseer thinks

- **Truthfulness:** Overseer never invents people, relationships, places, or facts. Every personal claim is tagged `[vault: <path>]`, `[inferred]`, or `[unknown]`. The vault is the source of truth.
- **Brain growth:** when you mention something new in conversation (a deal, a book, a person, a trip), Overseer either auto-writes it (atomic events) or proposes a write with a diff (entities, relationships).
- **New lobes:** if you tell Overseer something that doesn't fit any partition, it asks one line: *"I don't have a home for this — parking in inbox-novel/, ok?"* When 3+ similar quarantined items accumulate, Overseer proposes creating a new partition with schema and dashboard.

## Key bindings (TUI)

| key | action |
|---|---|
| `enter` | send |
| `ctrl+j` / `alt+enter` | newline |
| `esc` | cancel in-flight |
| `↑ ↓` | scroll messages |
| `pgup pgdn` | scroll page |
| `ctrl+n` | new thread |
| `ctrl+l` | list past sessions |
| `ctrl+c` | quit |

## Config

See [`.env.example`](.env.example). Required: `VAULT_PATH`, `OLLAMA_URL`, `OLLAMA_MODEL`. Everything else has sensible defaults.

## Repo layout

| Dir | Purpose |
|---|---|
| `api/` | FastAPI Overseer node (Python). Exposes `/chat`, `/chat/stream`, `/health`. |
| `tui-go/` | Bubble Tea TUI (Go). Single-binary terminal client. |
| `gateway/` | Always-on VPS router that brokers between multiple nodes (optional). |
| `agents/` | Background brain agents: tagger, linker, yap_processor. |
| `scripts/` | Utilities: `check-vault-schemas.py`, `novel_pattern_detector.py`, etc. |
| `templates/` | Vault scaffolds shipped to new users via `setup.sh`. |
| `workers/` | Cloudflare Worker edge layer (optional). |

## Vault schema

See your scaffolded `<vault>/VAULT.md` for the canonical schema. The short version: every wiki page belongs to exactly one of ~17 partitions, every page has a `sources:` frontmatter field (anti-hallucination), every entity type has a typed writer tool in the Overseer API.

## License

MIT. See [LICENSE](LICENSE).
