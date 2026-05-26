---
title: Sales
partition: sales
type: index
sources: [synthesized]
created: 2026-05-25
updated: 2026-05-25
tags: [sales, index]
---

# Sales

Pipeline tracking for leads, deals, and clients across all of Sam's revenue-generating work (Mad House consulting, Orinadus engagements, freelance, etc.).

## Layout

| Subfolder | Type | Purpose |
|---|---|---|
| `leads/` | `lead` | Top-of-funnel — names, source, first-contact context |
| `deals/` | `deal` | Active opportunities with value + status |
| `clients/` | `client` | Closed-won relationships, ongoing accounts |

## Schemas

**deal** — `wiki/sales/deals/<slug>.md`
```yaml
type: deal
partition: sales
client: <client-name>
status: lead | qualified | proposal | negotiation | won | lost
value: 0
currency: USD
opened: YYYY-MM-DD
close_date: YYYY-MM-DD
next_action: ""
```

**lead** — `wiki/sales/leads/<slug>.md`
```yaml
type: lead
partition: sales
source: ""
first_contact: YYYY-MM-DD
status: cold | warm | qualified
```

**client** — `wiki/sales/clients/<slug>.md`
```yaml
type: client
partition: sales
since: YYYY-MM-DD
mrr: 0
relationship: ""
```

See [[../../dashboards/sales|Sales dashboard]] for the live pipeline view.
