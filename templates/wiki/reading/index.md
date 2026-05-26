---
title: Reading
partition: reading
type: index
sources: [synthesized]
created: 2026-05-25
updated: 2026-05-25
tags: [reading, index]
---

# Reading

Books, articles, courses, podcasts. Subfolders by medium. Progress tracked via `status` and `progress_pct`.

## Layout

| Subfolder | Medium |
|---|---|
| `books/` | books |
| `articles/` | articles |
| `courses/` | courses |
| `podcasts/` | podcasts |

## Schema

```yaml
type: reading-item
partition: reading
medium: book | article | course | podcast
status: queue | reading | done | abandoned
author: ""
title: ""
progress_pct: 0
started: YYYY-MM-DD
finished: YYYY-MM-DD
notes: ""
```

See [[../../dashboards/reading|Reading dashboard]] for the three-column queue/reading/done view.
