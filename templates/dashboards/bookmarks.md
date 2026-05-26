---
title: Bookmarks Dashboard
tags: [dashboard, bookmarks]
---

# Bookmarks

## Recently saved

```dataview
TABLE title, url, topic, saved
FROM "wiki/bookmarks"
WHERE file.name != "index"
SORT saved DESC
LIMIT 30
```

## By topic

```dataview
TABLE length(rows) AS count
FROM "wiki/bookmarks"
WHERE file.name != "index" AND topic
GROUP BY topic
SORT length(rows) DESC
```

## Untagged

```dataview
LIST
FROM "wiki/bookmarks"
WHERE file.name != "index" AND (!topic OR topic = "")
LIMIT 20
```
