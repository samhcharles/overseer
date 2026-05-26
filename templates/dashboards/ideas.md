---
title: Ideas Dashboard
tags: [dashboard, ideas, inbox]
---

# Ideas Inbox

## Untriaged

```dataview
TABLE captured_at, file.link AS idea
FROM "wiki/inbox-ideas"
WHERE file.name != "index" AND triaged = false
SORT captured_at DESC
LIMIT 50
```

## Aged (>14 days, still untriaged)

```dataview
LIST
FROM "wiki/inbox-ideas"
WHERE file.name != "index" AND triaged = false AND file.ctime < date(today) - dur(14d)
SORT file.ctime ASC
```

## Triaged (recent — where did they go?)

```dataview
TABLE target_partition, file.mtime AS triaged_at
FROM "wiki/inbox-ideas"
WHERE file.name != "index" AND triaged = true
SORT file.mtime DESC
LIMIT 20
```

---

Out-of-schema captures live in [[../wiki/inbox-novel/index|inbox-novel]] — different problem, surfaced separately.
