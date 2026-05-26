---
title: Journal
partition: journal
type: index
sources: [synthesized]
created: 2026-05-25
updated: 2026-05-25
tags: [journal, index]
---

# Journal

Daily journal entries. Path: `journal/YYYY/MM/YYYY-MM-DD.md`. One entry per day. Mood + energy tracked as numerics for the timeline chart.

## Schema

```yaml
type: journal-entry
partition: journal
date: YYYY-MM-DD
mood: 1-10
energy: 1-10
key_events: []
gratitude: []
```

Body: free-form prose. The frontmatter numerics drive the dashboard timeline.

See [[../../dashboards/journal|Journal dashboard]] for the mood timeline and weekly average.
