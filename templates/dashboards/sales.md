---
title: Sales Dashboard
tags: [dashboard, sales]
---

# Sales

## Pipeline by value (active deals)

```dataview
TABLE client, status, value, currency, close_date, next_action
FROM "wiki/sales/deals"
WHERE status != "won" AND status != "lost"
SORT value DESC
```

## Won this quarter

```dataview
TABLE client, value, currency, close_date
FROM "wiki/sales/deals"
WHERE status = "won" AND close_date >= date(today) - dur(90d)
SORT close_date DESC
```

## Pipeline value by stage

```dataview
TABLE status, sum(value) AS total, length(rows) AS deals
FROM "wiki/sales/deals"
WHERE status != "won" AND status != "lost"
GROUP BY status
SORT sum(value) DESC
```

## Active leads

```dataview
TABLE source, first_contact, status
FROM "wiki/sales/leads"
WHERE status != "qualified"
SORT first_contact DESC
LIMIT 20
```

## Clients

```dataview
TABLE since, mrr, relationship
FROM "wiki/sales/clients"
SORT mrr DESC
```

## Pipeline Kanban

For drag-and-drop pipeline management, see [[sales-kanban]] (separate kanban file using obsidian-kanban plugin).
