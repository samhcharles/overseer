---
tags: [dashboard, projects]
---

# Projects

## Quick Links

- [[wiki/orinadus/MOC]] · [[wiki/madhouse/MOC]] · [[wiki/personal/MOC]]
- [[outputs/brain-research-pack-2026-05-19]] · [[outputs/second-brain-growth-2026-05-19]] · [[dashboards/founder-control]]

## Active Projects (cross-partition meta)

```dataview
TABLE owner_partition, status, priority, due
FROM "wiki/projects"
WHERE file.name != "index" AND status != "done" AND status != "archived"
SORT priority DESC, due ASC
```

## By owner partition

```dataview
TABLE status, priority, due
FROM "wiki/projects"
WHERE file.name != "index"
GROUP BY owner_partition
SORT owner_partition ASC
```

## Project Kanban

For drag-and-drop status board, see [[projects-kanban]] (separate kanban file).

## Orinadus pages

```dataview
TABLE file.link as "Note", status, file.mtime as "Updated"
FROM "wiki/orinadus"
SORT file.mtime DESC
LIMIT 20
```

## Mad House pages

```dataview
TABLE file.link as "Note", status, file.mtime as "Updated"
FROM "wiki/madhouse"
SORT file.mtime DESC
LIMIT 20
```

## Personal pages

```dataview
TABLE file.link as "Note", file.mtime as "Updated"
FROM "wiki/personal"
SORT file.mtime DESC
LIMIT 20
```

## Recent Outputs

```dataview
TABLE file.link as "Output", file.mtime as "Updated"
FROM "outputs"
SORT file.mtime DESC
LIMIT 10
```

## Founder Flow

![[dashboards/founder-control]]
