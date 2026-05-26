---
title: Inbox — Ideas
partition: inbox-ideas
type: index
sources: [synthesized]
created: 2026-05-25
updated: 2026-05-25
tags: [inbox, ideas, index]
---

# Inbox — Ideas

Quick-capture for raw thoughts before they're filed. Overseer triages these into their canonical partition over time (or leaves them here if they're one-offs).

## Schema

```yaml
type: idea
partition: inbox-ideas
captured_at: YYYY-MM-DDTHH:MM
triaged: false
target_partition: ""
```

Body: the idea, free-form.

Triage workflow:
1. Idea captured via `/idea <text>` slash command in Overseer TUI.
2. Overseer reviews periodically; when an idea clearly maps to a partition, it proposes a move.
3. Untriaged ideas older than N days surface in the home dashboard.

Sibling: [[../inbox-novel/index|inbox-novel/]] holds inputs that don't yet fit ANY partition (different problem — see [[../../dashboards/ideas|Ideas dashboard]]).
