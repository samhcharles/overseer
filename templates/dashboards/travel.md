---
title: Travel Dashboard
tags: [dashboard, travel]
---

# Travel

## Upcoming trips

```dataview
TABLE destination, start_date, end_date
FROM "wiki/travel/trips"
WHERE start_date >= date(today)
SORT start_date ASC
```

## Currently traveling

```dataview
TABLE destination, end_date
FROM "wiki/travel/trips"
WHERE start_date <= date(today) AND end_date >= date(today)
```

## Past trips

```dataview
TABLE destination, start_date, end_date
FROM "wiki/travel/trips"
WHERE end_date < date(today)
SORT end_date DESC
LIMIT 30
```

## Places visited (link to map)

For the dotted map view, see [[places|Places dashboard]].
