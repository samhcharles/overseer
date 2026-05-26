---
title: Projects
partition: projects
type: index
sources: [synthesized]
created: 2026-05-25
updated: 2026-05-25
tags: [projects, index]
---

# Projects

Cross-partition project meta — one page per active project. Lives separately from `wiki/orinadus/`, `wiki/madhouse/`, `wiki/personal/` (those hold canonical company/personal content). This partition is the kanban-friendly overlay.

## Schema

```yaml
type: project-meta
partition: projects
owner_partition: orinadus | madhouse | personal | other
status: now | next | waiting | done | archived
priority: low | medium | high
started: YYYY-MM-DD
due: YYYY-MM-DD
linked_pages: []
```

Body: short status line, next action, blockers.

See [[../../dashboards/projects|Projects dashboard]] for the kanban grouped by `owner_partition`.
