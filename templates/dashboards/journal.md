---
title: Journal Dashboard
tags: [dashboard, journal]
---

# Journal

## Recent entries (mood + energy)

```dataview
TABLE date, mood, energy, key_events
FROM "wiki/journal"
WHERE file.name != "index"
SORT date DESC
LIMIT 30
```

## Mood timeline (last 30 days)

```dataview
TABLE date, mood, choice(mood >= 8, "🟢🟢🟢🟢", choice(mood >= 6, "🟢🟢🟢", choice(mood >= 4, "🟡🟡", "🔴"))) AS bar
FROM "wiki/journal"
WHERE file.name != "index" AND date >= date(today) - dur(30d)
SORT date ASC
```

## Weekly averages (last 8 weeks)

```dataview
TABLE dateformat(date, "yyyy-'W'WW") AS week, round(average(mood), 1) AS mood_avg, round(average(energy), 1) AS energy_avg
FROM "wiki/journal"
WHERE file.name != "index" AND date >= date(today) - dur(60d)
GROUP BY dateformat(date, "yyyy-'W'WW")
SORT week DESC
```

## Gratitude tag cloud (last 14 days)

```dataview
LIST gratitude
FROM "wiki/journal"
WHERE file.name != "index" AND date >= date(today) - dur(14d) AND gratitude
SORT date DESC
```
