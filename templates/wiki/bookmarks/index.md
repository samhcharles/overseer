---
title: Bookmarks
partition: bookmarks
type: index
sources: [synthesized]
created: 2026-05-25
updated: 2026-05-25
tags: [bookmarks, index]
---

# Bookmarks

Saved URLs with metadata. Flat folder — one file per bookmark. Grouped by `topic` in dashboards, not by filesystem path.

## Schema

`wiki/bookmarks/<slug>.md`
```yaml
type: bookmark
partition: bookmarks
url: https://...
title: ""
topic: ""
source: ""
summary: ""
tags: []
saved: YYYY-MM-DD
```

See [[../../dashboards/bookmarks|Bookmarks dashboard]] for the grid + topic-filter view.
