---
title: Contacts
partition: contacts
type: index
sources: [synthesized]
created: 2026-05-25
updated: 2026-05-25
tags: [contacts, index]
---

# Contacts

Business contacts, vendors, suppliers. Distinct from `wiki/personal/people/` (which holds family, friends, close personal relationships).

## Layout

| Subfolder | Purpose |
|---|---|
| `business/` | clients, partners, collaborators |
| `vendors/` | service providers (legal, accounting, hosting) |
| `suppliers/` | physical goods + materials |

## Schema

```yaml
type: contact
partition: contacts
org: ""
role: ""
relationship: ""
email: ""
phone: ""
last_contact: YYYY-MM-DD
next_touch: YYYY-MM-DD
notes: ""
```

See [[../../dashboards/contacts|Contacts dashboard]] for grouping by relationship and overdue-touch list.
