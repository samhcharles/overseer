# VAULT — Schema & Operating Instructions

Every agent working in this vault reads this file first. This is the constitution.
When in doubt, this file governs. If you find a conflict, flag it and update this file.

---

## What this vault is

A sovereign, offline-first personal operating system. Not RAG. Not a note dump.
All data is local. Nothing leaves this machine except to a thumb drive backup.
Every claim traces back to a source. The wiki is compiled from raw data by agents. You read it. Agents write it.

Core principle (Karpathy): "The LLM is the programmer. The wiki is the codebase. Obsidian is the IDE."

---

## The three-layer pipeline

```
CAPTURE (Urchin)
  Collectors run: shell, git, calendar, health, bank CSV, etc.
  Everything writes to the JSONL journal only.
  ~/.local/share/urchin/journal/events.jsonl
  Urchin never touches this vault.

DISTILLATION (Sleipnir)
  Reads the Urchin journal continuously (byte-offset cursor).
  Filters noise, clusters events into activity sessions.
  Writes markdown stubs to inbox/sleipnir/ — one per cluster.
  Persists clusters to ~/.local/share/urchin/sleipnir.db.
  Sleipnir only writes to inbox/sleipnir/. Nothing else.

INTELLIGENCE (Overseer)
  Reads the vault. Processes the inbox. Writes structured pages.
  Runs locally via Ollama (fine-tuned Qwen2.5-3B on this vault).
  No internet required. No data leaves this machine.
  Overseer is the only agent that writes to wiki/, daily/, memory/.
```

---

## Navigation anchors

- [[CORES]] — global map of the five cores
- [[agents/MOC]] — assistant overlay hub for the nested agent brains
- [[wiki/_index]] — wiki catalog
- [[wiki/_log]] — append-only operation history
- [[systems/MOC]] — infrastructure hub
- [[systems/overseer]] — AI and memory bridge

---

## Directory structure

| Path | Owner | Rule |
|------|-------|------|
| `raw/` | Human + agents (ingest only) | Immutable after ingest. Never edited. Source of truth. |
| `wiki/` | Overseer + agents | Agents create and update. Human reads. |
| `wiki/_index.md` | Overseer | Catalog of ALL wiki pages. Updated on every ingest. |
| `wiki/_log.md` | Overseer | Append-only operation log. Never edited, only appended. |
| `daily/` | Overseer | YYYY-MM-DD.md daily summaries. Written from Sleipnir stubs + yap. |
| `inbox/` | Agents + human | Staging zone. Unprocessed captures. Compiled into wiki/ on schedule. |
| `inbox/sleipnir/` | Sleipnir | Activity cluster stubs. One .md per cluster. Overseer processes these. |
| `inbox/sessions/` | session-close skill | Raw Claude Code session debriefs. Overseer compiles into wiki/sessions/. |
| `inbox/yap/` | Overseer / human | Raw voice or text dumps. Immutable after creation. |
| `memory/` | Overseer only | Overseer's persistent memory. No other agent writes here. |
| `memory/facts/` | Overseer | Discrete facts: people.md, preferences.md, recurring.md. |
| `memory/overseer-live.md` | Overseer | Current Overseer activity trace. Refreshed each session. |
| `outputs/` | Agents | Q&A answers, reports, analyses. Good outputs filed back into wiki/. |
| `dashboards/` | Human | Obsidian Dataview query notes. Read-only views over vault data. |
| `templates/` | Human | Obsidian Templater templates. |
| `wiki/agents/` | Agents | AI brain memory files. All AI tools merge their memory here. |

---

## Partitions

Every wiki page belongs to exactly ONE partition. Tag with frontmatter `partition:`.

| Partition | Path | What belongs here |
|-----------|------|-------------------|
| `orinadus` | `wiki/orinadus/` | Urchin, Sleipnir, orinadus-platform, architecture, roadmap |
| `madhouse` | `wiki/madhouse/` | Chopsticks, agents, projects, hermes, experiments, brand |
| `personal` | `wiki/personal/` | Goals, relationships, identity, creative work, preferences |
| `finance` | `wiki/finance/` | Transactions, budgets, accounts, invoices, client billing |
| `health` | `wiki/health/` | Daily metrics, Apple Health data, sleep, fitness, trends |
| `calendar` | `wiki/calendar/` | Events, appointments, recurring schedules |
| `places` | `wiki/places/` | Location history, venues, maps, visits |
| `knowledge` | `wiki/knowledge/` | External research, learnings not tied to a company |
| `systems` | `wiki/systems/` | Infrastructure, dev setup, machine roles, tooling |
| `sales` | `wiki/sales/` | Pipeline — leads, deals, clients. Cross-revenue (Mad House, Orinadus, freelance) |
| `bookmarks` | `wiki/bookmarks/` | Saved URLs with topic, summary, tags |
| `reading` | `wiki/reading/` | Books, articles, courses, podcasts — with status + progress |
| `journal` | `wiki/journal/` | Daily journal entries with mood + energy + key events |
| `projects` | `wiki/projects/` | Cross-partition project meta (kanban overlay) |
| `contacts` | `wiki/contacts/` | Business contacts, vendors, suppliers (distinct from `personal/people/`) |
| `travel` | `wiki/travel/` | Trips, itineraries, places-visited timelines |
| `inbox-ideas` | `wiki/inbox-ideas/` | Quick-capture ideas pending triage into a canonical partition |
| `inbox-novel` | `wiki/inbox-novel/` | Quarantine for inputs that don't fit ANY existing partition. Brain grows new lobes from here. |

Cross-partition links are allowed. Content is NEVER duplicated across partitions. Link, don't copy.

**Identity note:** Mad House and Orinadus are fully separate companies with no relation. Overseer itself is Sam's personal project — not owned by either company. Never blend the two in any wiki page or agent output.

### Overlay clusters

- `wiki/agents/` is an overlay cluster. Use `partition: systems`.
- `wiki/agents/` describes role, interface, memory shape, routing. Implementation facts belong in canonical owner pages.
- AI tool memory files (CLAUDE.md, AGENTS.md, cursor rules, etc.) are merged here as they accumulate.

---

## Frontmatter convention

Every wiki page must have:

```yaml
---
title: Page Title
partition: orinadus | madhouse | personal | finance | health | calendar | places | knowledge | systems
type: optional-type-hint
sources:
  - raw/sessions/2026-05-22-claude-abc.md
created: YYYY-MM-DD
updated: YYYY-MM-DD
tags: [partition-name, topic]
---
```

`sources` is critical. Every wiki page must list where it came from.
A page with no `sources` is a hallucination risk — flag it in vault-health.

Domain-specific required frontmatter fields:

**Finance transactions:**
```yaml
amount: 0.00
currency: USD
merchant: Name
category: food | transport | software | client | other
account: checking | savings | card
date: YYYY-MM-DD
```

**Health daily:**
```yaml
date: YYYY-MM-DD
steps: 0
sleep_hours: 0.0
weight_kg: 0.0
notes: ""
```

**Calendar events:**
```yaml
date: YYYY-MM-DD
time: HH:MM
duration_mins: 60
location: ""
attendees: []
recurring: false
```

**Places visits:**
```yaml
date: YYYY-MM-DD
lat: 0.0
lng: 0.0
name: Place Name
city: City
duration_mins: 0
```

---

## Operations

### INGEST
Triggered: new item in raw/ or inbox/.

1. Read VAULT.md
2. Read wiki/_index.md to understand existing pages
3. Read the source
4. Determine which wiki pages to create or update
5. Write/update with proper frontmatter (include source reference)
6. Update wiki/_index.md with any new pages
7. Append to wiki/_log.md: `[YYYY-MM-DD] ingest | <source> | <N pages touched>`

### SLEIPNIR PROCESS
Triggered: new stubs in inbox/sleipnir/.

1. Read each stub (activity cluster: source, time range, event sample)
2. Update or create today's daily/YYYY-MM-DD.md with an activity section
3. If the session is notable (long duration, specific project), create wiki/sessions/YYYY-MM-DD-slug.md
4. Append to wiki/_log.md: `[YYYY-MM-DD] sleipnir | <source> <duration> | daily/ updated`

### CONVERSATION
Triggered: anytime you talk to Overseer.

Overseer is a fine-tuned model that knows this vault, its schema, and your life.
There is no rigid extraction pipeline. Overseer understands what you say and decides
what to remember, where to put it, and what to update — the same way you would if you
were organizing your own notes. It reads the vault schema (this file) and acts accordingly.

If you mention a transaction, it writes a transaction note.
If you mention a place, it writes a visit note.
If you talk about your health, it updates health/daily/.
If you're just thinking out loud, it might write nothing and just respond.

The only rule: if something is written, it goes to the right partition with correct frontmatter.
Append to wiki/_log.md when something meaningful is stored.

### QUERY
Triggered: user asks Overseer a question.

1. Read wiki/_index.md to find relevant pages
2. Read those pages
3. Synthesize answer — cite wiki pages, which cite raw/ sources
4. If answer is valuable: file as outputs/YYYY-MM-DD-slug.md, consider adding to wiki/
5. Append to wiki/_log.md: `[YYYY-MM-DD] query | <question summary> | <pages read>`

### LINT
Triggered: on demand or weekly.

Check for:
- Wiki pages with no `sources` frontmatter
- Orphan pages (no inbound links)
- Finance transactions older than 30 days with no category
- Health notes with missing fields
- inbox/sleipnir/ stubs older than 24h (not yet processed)
- inbox/sessions/ stubs older than 7 days
- Contradictions between pages in the same partition

Output: outputs/health-YYYY-MM-DD.md. Append to _log.md.

---

## What agents may NOT do

- Never delete a wiki page. Archive: add `archived: true` frontmatter, move to wiki/archive/.
- Never edit raw/ files after creation.
- Never write to memory/ except Overseer.
- Never create a wiki page without `sources` unless explicitly flagged as `sources: [synthesized]`.
- Never copy content across partition boundaries. Link instead.
- Never guess facts. Use `[VERIFY]` tag inline and flag in _log.md.

---

## Anti-hallucination rules

1. Every claim in wiki/ must trace to raw/ or a direct human statement (yap / session).
2. When wiki pages compile from other wiki pages, those must themselves have raw/ sources.
3. Circular synthesis (wiki/ → wiki/ → wiki/) is not allowed beyond 1 hop.
4. vault-health flags any page where the source chain cannot be resolved.
5. `[VERIFY]` tags are surfaced in every vault-health run until resolved.

---

## Urchin integration

Urchin captures events to `~/.local/share/urchin/journal/events.jsonl`.
Urchin does NOT write to this vault. It is a capture layer only.

Collectors that feed Urchin (and therefore eventually the vault via Sleipnir):
- Shell activity (commands run)
- Git commits and diffs
- Calendar events (ICS/iCal)
- Apple Health export (steps, sleep, HRV, weight)
- Bank CSV (transactions)
- ProtonMail Bridge (flagged emails)
- Browser activity (opt-in)

---

## Sleipnir integration

Sleipnir is the distillation daemon between Urchin and this vault.

- Runs every 30 seconds on the local machine
- Reads from `~/.local/share/urchin/journal/events.jsonl` via byte-offset cursor
- Filters noise, deduplicates, clusters by source and time gap (5-minute idle = new cluster)
- Writes cluster stubs to `inbox/sleipnir/YYYY-MM-DD-HHmm-source.md`
- Overseer processes those stubs into daily/ notes and wiki/sessions/ pages

Stub frontmatter: `partition: sessions`, `type: activity-cluster`, `source:`, `started_at:`, `ended_at:`, `duration_secs:`, `event_count:`, `event_ids:`.

---

## Overseer

Overseer is the local intelligence layer. Runs on this machine via Ollama (fine-tuned Qwen2.5-3B).
No internet required. No API keys. No data leaves this machine.

| Endpoint | Input | Output |
|---|---|---|
| `POST /chat` | `{message, thread_id?}` | Streaming response, full tool access |
| `POST /triage` | `{content, source}` | `{tags, destination, summary, entities}` |
| `POST /remember` | `{fact, category}` | Writes to memory/facts/[category].md |
| `GET /recall?q=` | query string | Relevant vault context |
| `GET /health` | — | `{model, status, vault_stats}` |

Overseer tools:
- `vault_read(path)` — read a vault file
- `vault_write(path, content)` — write a vault file
- `vault_search(query)` — ripgrep across vault
- `list_notes(folder)` — list files in a folder
- `sleipnir_query(hours, source?)` — query recent activity clusters from Sleipnir SQLite

Overseer is the only writer to memory/. It is the only agent that materializes Sleipnir stubs into daily/ notes.

---

## Sync

| Layer | Method |
|-------|--------|
| Primary | Local filesystem on this machine (`~/vault/`) |
| Backup | Thumb drive — manual, on demand |

No cloud sync. No GitHub. No remote server. The vault is yours and stays on your machine.
Obsidian reads from `~/vault/` directly on WSL and the mounted path on Windows.
