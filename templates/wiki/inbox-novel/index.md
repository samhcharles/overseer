---
title: Inbox — Novel (out-of-schema captures)
partition: inbox-novel
type: index
sources: [synthesized]
created: 2026-05-25
updated: 2026-05-25
tags: [inbox, novel, index]
---

# Inbox — Novel

Quarantine for inputs Overseer can't confidently file into any existing partition. Different from [[../inbox-ideas/index|inbox-ideas]]: ideas are "fits a partition, not triaged yet"; novel is "doesn't fit ANY partition we have right now."

The brain grows new lobes from this folder. When ≥3 items share an extracted entity type or topic, Overseer proposes a new partition with schema + dashboard.

## Schema

```yaml
type: novel-capture
partition: inbox-novel
captured_at: YYYY-MM-DDTHH:MM
raw_text: ""
extracted_entities: []
confidence_score: 0.0
triaged: false
target_partition: ""
```

Body: the raw capture, untouched.

## Capture UX

Overseer asks ONE line when an input doesn't match any schema:
`I don't have a home for this — parking in inbox-novel/, ok?`

Enter or `y` writes. `n` opens a partition picker.

## Cluster genesis

Daily cron `scripts/novel_pattern_detector.py` scans for 3+ similar items. Proposes new partition next session. Always asks before creating. Rejected proposals decay (don't refire for N days). Even after a cluster is born, `inbox-novel/` persists — some captures are genuinely one-offs.
