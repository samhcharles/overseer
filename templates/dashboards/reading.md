---
title: Reading Dashboard
tags: [dashboard, reading]
---

# Reading

## Currently reading

```dataview
TABLE medium, author, progress_pct, started
FROM "wiki/reading"
WHERE status = "reading"
SORT started DESC
```

## Queue

```dataview
TABLE medium, author
FROM "wiki/reading"
WHERE status = "queue"
SORT file.ctime DESC
```

## Finished this year

```dataview
TABLE medium, author, finished
FROM "wiki/reading"
WHERE status = "done" AND finished >= date(today) - dur(365d)
SORT finished DESC
```

## Abandoned

```dataview
LIST
FROM "wiki/reading"
WHERE status = "abandoned"
```
