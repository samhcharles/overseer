---
title: Contacts Dashboard
tags: [dashboard, contacts]
---

# Contacts

## By relationship

```dataview
TABLE org, role, last_contact, next_touch
FROM "wiki/contacts"
WHERE file.name != "index"
GROUP BY relationship
SORT relationship ASC
```

## Overdue touch (next_touch in the past)

```dataview
TABLE org, role, next_touch
FROM "wiki/contacts"
WHERE file.name != "index" AND next_touch AND next_touch < date(today)
SORT next_touch ASC
```

## Recently added

```dataview
TABLE org, role, relationship
FROM "wiki/contacts"
WHERE file.name != "index"
SORT file.ctime DESC
LIMIT 15
```

## No last_contact recorded

```dataview
LIST
FROM "wiki/contacts"
WHERE file.name != "index" AND (!last_contact OR last_contact = "")
```
